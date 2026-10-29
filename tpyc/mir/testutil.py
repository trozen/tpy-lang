"""A bounded MIR interpreter with explicit shared object identities."""

from collections.abc import Callable, Collection, Iterable, Mapping
from contextlib import nullcontext
from dataclasses import dataclass, replace
import operator
from types import MappingProxyType

from ..compilation_context import activate_compiler
from ..mir_workspace import MIRCallWorkspace, analyze_call_workspace
from ..type_def_registry import TypeCategory, TypeDef, _type_defs, int_traits_of, register
from ..typesys import INT32_MIN, INT32_MAX, FieldInfo, NominalType, RecordInfo

from .nodes import (
    MIRSlot, MIRAlias, MIRAssign, MIRBranch, MIRCall, MIRCallStmt, MIRCompare, MIRConstant, MIRFunction, MIRGoto, MIRNot, MIRPlace, MIRRead,
    MIRReturn, MIRSlotKind, MIRGlobalId, MIRBorrow, MIRConstruct, MIRCopy, MIRMove, MIRSlotId,
    MIRStorageWrite, MIRStorageWriteMode, MIRStorageInit,
    MIREdge, MIRIsAlternative, MIRUnionExtract, MIRIteratorInit, MIRIteratorHasNext, MIRIteratorRead,
    MIRIteratorAdvance, MIRRangeAdvance, MIROp, MIRPrint, MIRSuspend, place_shape, MIRWrap, MIRNotCovered, MIRBodyId,
    builds_base, statement_call,
)
from .value import (
    NO_FIELDS, Declared, MIRField, MIRFieldId, MIRHolding, MIRRecordLayout, MIRRecords, MIRShape, RecordFields,
    WRAPPER_CLASSES, loan_field,
)
from .region_flow import MIRRegionFlow
from ..thir import nodes as th
from ..thir.scalar_leaves import MIRClass, value_class
from ..type_def_registry import ParamPassing
from ..typesys import OwnType, TpyType, return_representation
from .call_contract import (
    FUNCTION, MIR_RESULT, MIRCallSummary, MIRLoanTransfer, MIRParameterBinding, parameter_binding,
)
from .definitions import MIRDefinitions, constructor_identity, declared_fields
from .shapes import MIRContainerElements, MIRDeref, MIRTupleIndex, MIRUnionPayload
from .validate import statement_reads
from .prepare import MIRPrepared, prepare
from . import lower as _lower
from .collect import dump_codegen_mir
from .lower import MIRLoweredStorage

# The TypeDef of every hand-built record (`hand_record`): its declared
# fields are what `declared_fields` resolves from it.
_HAND_TYPE_DEFS: dict[str, TypeDef] = {}


def hand_record(name: str, qname: str, /, *bases: NominalType, **fields: TpyType) -> NominalType:
    """A record a test builds MIR over by hand, declaring `fields` and
    deriving from the hand-built `bases`, with the plain user record
    TypeDef MIR admits a record by and reads its declared fields from. The
    TypeDef is in the registry while the MIR tests are collected and while
    each runs (`register_hand_records`), never beside other tests."""
    typ = NominalType(name, _module_qname=qname)
    declared = [FieldInfo(n, t) for n, t in fields.items()]
    ancestors = [a for base in bases for a in (base, *_HAND_TYPE_DEFS[base.qualified_name()].record.mro_ancestors)]
    if qname not in _HAND_TYPE_DEFS:
        _HAND_TYPE_DEFS[qname] = TypeDef(qname=qname, category=TypeCategory.RECORD, record=RecordInfo(
            name, declared, module=qname.rsplit(".", 1)[0], parents=list(bases), mro_ancestors=ancestors))
        register(_HAND_TYPE_DEFS[qname])
    record = _HAND_TYPE_DEFS[qname].record
    assert record.fields == declared and record.mro_ancestors == ancestors, (typ, fields)
    return typ


def register_hand_records() -> None:
    for qname, td in _HAND_TYPE_DEFS.items():
        if qname not in _type_defs:
            register(td)


def unregister_hand_records() -> None:
    for qname, td in _HAND_TYPE_DEFS.items():
        if _type_defs.get(qname) is td:
            del _type_defs[qname]


# Shapes for hand-built slots and members, one per holding: the class is
# the type's (`value_class`), unless a probe of an ill-classed value names it.
def _class(typ: TpyType, cls: MIRClass | None) -> MIRClass:
    found = cls if cls is not None else value_class(typ)
    return found if found is not None else MIRClass.RECORD


_SHAPE_FIELDS = frozenset({"type", "cls", "holding", "readonly", "members"})


def bound(typ: TpyType, passing: ParamPassing, readonly: bool = False,
          ref: th.THIRBorrowedRecord | None = None) -> MIRParameterBinding:
    """The binding `parameter_binding` derives for a hand-built summary's
    parameter of declared type `typ` (at access `readonly` where THIR
    publishes no fact)."""
    binding = parameter_binding(th.THIRParam("", typ, borrowed_record=ref, passing=passing),
                                Declared(readonly) if ref is None else FUNCTION)
    assert isinstance(binding, MIRParameterBinding), binding
    return binding


def reaccess(binding: MIRParameterBinding, readonly: bool) -> MIRParameterBinding:
    """A forged binding: `binding` with its value at access `readonly`."""
    return replace(binding, value=replace(binding.value, readonly=readonly))


def bindings_of(fn: th.THIRFunction) -> tuple[MIRParameterBinding, ...]:
    """The parameter bindings a lowering of `fn` records, receiver first (a hand-built body's)."""
    bindings = tuple(parameter_binding(p) for p in th.effective_params(fn))
    assert all(isinstance(b, MIRParameterBinding) for b in bindings), bindings
    return bindings


def _member_fact(member: MIRShape | None, element: bool = False) -> object:
    """The THIR fact a layout lays member `member` out as (the inverse of
    `thir_member`); a container's record element is a borrowed one."""
    if member is None or member.cls is not MIRClass.RECORD:
        return None if member is None else member.type
    borrowed = element or member.is_pointer
    return (th.THIRBorrowedRecord if borrowed else th.THIROwnedRecord)(member.type, member.readonly)


def slot_param(slot: MIRSlot, passing: ParamPassing | None = None) -> th.THIRParam:
    """The THIR parameter a hand-built parameter slot is the slot of: its
    type (handed over, `Own[...]`, when the slot is its own storage), its
    passing (the type's own at the slot's access, unless `passing` states
    one) and the fact its shape lays out."""
    shape = slot.shape
    facts: dict[str, object] = {}
    match shape.cls:
        case MIRClass.RECORD if shape.is_pointer:
            facts["borrowed_record"] = th.THIRBorrowedRecord(shape.type, shape.readonly)
        case MIRClass.CONTAINER | MIRClass.CONTAINER_VIEW if shape.members:
            facts["native_container"] = th.THIRNativeContainer(
                shape.type, _member_fact(shape.element, element=True), shape.readonly)
        case MIRClass.OPTIONAL:
            facts["optional_layout"] = th.THIROptionalLayout(_member_fact(shape.payload))
        case MIRClass.TUPLE:
            facts["tuple_layout"] = th.THIRTupleLayout(tuple(map(_member_fact, shape.members)))
        case MIRClass.UNION:
            facts["union_layout"] = th.THIRUnionLayout(shape.type, tuple(map(_member_fact, shape.members)))
    typ = OwnType(shape.type) if shape.is_storage else shape.type
    if passing is None:
        passing = typ.param_passing(shape.readonly)
    return th.THIRParam(slot.name or "", typ, passing=passing, **facts)


def records_of(*layouts: MIRRecordLayout) -> MIRRecords:
    """What a walk over hand-built records reads: their layouts, and the
    fields their records declare (`declared_fields`)."""
    return MIRRecords(MappingProxyType({layout.type: layout for layout in layouts}), declared_fields())


# The bodies of the constructors `constructor_summaries` lowered, which
# `execute` runs where a construction calls one.
_CONSTRUCTOR_BODIES: dict[int, MIRFunction] = {}
_CONSTRUCTOR_SUMMARIES: dict[int, tuple[MIRDefinitions, Mapping[th.THIRFunctionIdentity, object]]] = {}


def constructor_summaries(definitions: MIRDefinitions) -> Mapping[th.THIRFunctionIdentity, object]:
    """The summaries of the constructors `definitions` certifies, from a
    call workspace over those constructors alone: what a construction
    `R(...)` calls, for a test lowering a body with no workspace of its own."""
    cached = _CONSTRUCTOR_SUMMARIES.get(id(definitions))
    if cached is not None and cached[0] is definitions:
        return cached[1]

    constructors = tuple(d.constructor for d in definitions.records.values()
                         if not isinstance(d, str) and isinstance(d.constructor, th.THIRConstructor))
    # The lowering reads the TypeDefs of the compilation that verified them.
    with activate_compiler(definitions.compiler) if definitions.compiler is not None else nullcontext():
        workspace = analyze_call_workspace(tuple(
            (MIRBodyId("constructors", f"{c.record_name}.__init__#{i}"), c) for i, c in enumerate(constructors)),
            definitions)
    _CONSTRUCTOR_SUMMARIES[id(definitions)] = (definitions, workspace.summaries)
    for identity, (body, _source) in workspace.definitions.items():
        lowered = workspace.bodies.get(body)
        result = workspace.summaries[identity]
        if lowered is not None and not isinstance(lowered, MIRNotCovered) and result.summary is not None:
            # Keyed by the summary object a caller's call holds: identities repeat across compilations.
            _CONSTRUCTOR_BODIES[id(result.summary)] = lowered.function
    return workspace.summaries


def _summaries(definitions: MIRDefinitions | None, summaries: Mapping[th.THIRFunctionIdentity, object] | None
               ) -> Mapping[th.THIRFunctionIdentity, object] | None:
    """The summaries a test lowers a body against: its own, else those of
    the constructors `definitions` certifies (`constructor_summaries`)."""
    if summaries is not None or definitions is None:
        return summaries
    return constructor_summaries(definitions)


# A test lowers one body with no call workspace of its own: these lower it as
# `lower`'s entry points do, a construction calling its constructor's
# summary (`_summaries`). Run under the compilation that verified the
# definitions, whose TypeDefs the lowering reads.

def lower_function(fn: th.THIRFunction, body: MIRBodyId, *, definitions: MIRDefinitions | None = None,
                   summaries: Mapping[th.THIRFunctionIdentity, object] | None = None) -> MIRFunction | MIRNotCovered:
    return _lower.lower_function(fn, body, definitions=definitions, summaries=_summaries(definitions, summaries))


def lower_function_storage(fn: th.THIRFunction, body: MIRBodyId, *, definitions: MIRDefinitions | None = None,
                           summaries: Mapping[th.THIRFunctionIdentity, object] | None = None
                           ) -> MIRLoweredStorage | MIRNotCovered:
    return _lower.lower_function_storage(fn, body, definitions=definitions,
                                         summaries=_summaries(definitions, summaries))


def lower_constructor(ctor: th.THIRConstructor, body: MIRBodyId, *, definitions: MIRDefinitions | None = None,
                      summaries: Mapping[th.THIRFunctionIdentity, object] | None = None) -> MIRFunction | MIRNotCovered:
    return _lower.lower_constructor(ctor, body, definitions=definitions, summaries=_summaries(definitions, summaries))


def lower_constructor_storage(ctor: th.THIRConstructor, body: MIRBodyId, *,
                              definitions: MIRDefinitions | None = None,
                              summaries: Mapping[th.THIRFunctionIdentity, object] | None = None
                              ) -> MIRLoweredStorage | MIRNotCovered:
    return _lower.lower_constructor_storage(ctor, body, definitions=definitions,
                                            summaries=_summaries(definitions, summaries))


def dump_compiled_mir(compiler: object, entry: object, ctx: object) -> str:
    """The `--dump-mir` text of a compiled module, over the program's
    definitions and call workspace as the CLI builds them."""
    with compiler.mir_analysis(((entry, ctx),)) as mir:
        return dump_codegen_mir(entry.ast, entry.analyzer, ctx, entry.name, mir.definitions,
                                compiler.thir_reject_by_node, mir.workspace)


def compiled_definitions(compiler: object, constructors: 'tuple[th.THIRConstructor, ...]') -> MIRDefinitions:
    """The definitions of a compilation's constructors, verified under it."""
    with activate_compiler(compiler):
        return MIRDefinitions(constructors)


def as_declared(records: MIRRecords, layout: MIRRecordLayout) -> MIRRecords:
    """A forged record: `records` with `layout` added or replaced and its
    record declaring exactly the layout's fields."""
    fields = frozenset(layout.fields)
    return MIRRecords(MappingProxyType({**records, layout.type: layout}),
                      Redeclared(records.fields, lambda typ, declared: fields if typ == layout.type else declared))


def no_layouts() -> MIRRecords:
    """A walk over no layout: the declared fields alone."""
    return MIRRecords(MappingProxyType({}), declared_fields())


class Redeclared:
    """A test's own declared fields (a `RecordFields` lookup), disagreeing
    with the records' own: each record's as `change` gives it from
    `base`'s."""

    def __init__(self, base: RecordFields,
                 change: Callable[[TpyType, frozenset[MIRField]], frozenset[MIRField]]) -> None:
        self.base, self.change = base, change

    def __getitem__(self, typ: TpyType, /) -> frozenset[MIRField]:
        return self.change(typ, frozenset(self.base.get(typ, frozenset())))

    def get(self, typ: TpyType, default: Collection[MIRField] | None = None, /) -> Collection[MIRField] | None:
        return self[typ]


def with_fields(base: RecordFields, extra: 'Iterable[MIRField]') -> RecordFields:
    """`base` with `extra` added to their owners' declared fields."""
    added: dict[TpyType, frozenset[MIRField]] = {}
    for f in extra:
        added[f.id.owner] = added.get(f.id.owner, frozenset()) | {f}
    return Redeclared(base, lambda typ, declared: declared | added.get(typ, frozenset()))


# The record layout of each hand-built constructor summary (`construction`),
# which `execute` builds the record from.
_HAND_CONSTRUCTORS: dict[int, MIRRecordLayout] = {}
_HAND_SUMMARIES: dict[tuple[MIRRecordLayout, bool], MIRCallSummary] = {}


def construction(layout: MIRRecordLayout, *arguments: MIRSlotId, may_raise: bool = False) -> MIRCall:
    """A hand-built construction of record `layout`: a call of its
    constructor, one parameter per field taking the field's value -- a
    scalar by value, a view member storing the loan its argument holds (a
    transfer into the result) -- and handing over the record built."""
    key = (layout, may_raise)
    summary = _HAND_SUMMARIES.get(key)
    if summary is None:
        params = tuple(th.THIRParam(f"p{i}", f.type, passing=f.type.param_passing(False))
                       for i, f in enumerate(layout.fields))
        bindings = tuple(parameter_binding(p) for p in params)
        assert all(isinstance(b, MIRParameterBinding) for b in bindings), bindings
        result = OwnType(layout.type)
        signature = th.THIRCallableSignature(tuple(p.type for p in params), result,
                                             passings=tuple(p.passing for p in params),
                                             return_representation=return_representation(result))
        callee = th.THIRResolvedCallee(constructor_identity(layout.type), signature)
        transfers = frozenset(
            MIRLoanTransfer(MIR_RESULT, (th.THIRFieldIdentity(f.id.owner, f.id.name, f.type),), i)
            for i, f in enumerate(layout.fields) if loan_field(f.type))
        summary = _HAND_SUMMARIES[key] = MIRCallSummary(
            callee, bindings, frozenset(range(len(params))), frozenset(), frozenset(), frozenset(), transfers,
            not may_raise)
        _HAND_CONSTRUCTORS[id(summary)] = layout
    return MIRCall(summary, tuple(arguments), may_raise)


def with_calls(fn: MIRFunction) -> MIRFunction:
    """A hand-built body with the summaries of the calls it makes."""
    found = {id(s): s for s in fn.call_summaries}
    for block in fn.blocks:
        for stmt in block.statements:
            if (call := statement_call(stmt)) is not None:
                found.setdefault(id(call.summary), call.summary)
    return replace(fn, call_summaries=tuple(found.values()))


def bind_parameters(fn: MIRFunction, passings: dict[MIRSlotId, ParamPassing] | None = None, *,
                    fields: 'Iterable[MIRField]' = ()) -> MIRFunction:
    """A hand-built body with the binding the one derivation
    (`parameter_binding`) gives each of its parameter slots, at the passing
    `passings` names for it, else its type's own, and, unless it states
    them, the fields its records declare (`declared_fields`), with
    `fields` added to their owners', and the representation its declared
    return type has (as a body's lowering decides it with no signature)."""
    passings = passings or {}
    bindings = []
    for slot in fn.slots:
        if slot.kind is MIRSlotKind.PARAMETER:
            binding = parameter_binding(slot_param(slot, passings.get(slot.id)))
            # A slot no binding derives (a forged one) keeps none: the validator refuses it.
            if isinstance(binding, MIRParameterBinding):
                bindings.append(binding)
    declared = fn.record_fields if fn.record_fields is not NO_FIELDS else declared_fields()
    representation = (fn.return_representation if fn.return_representation is not None
                      else return_representation(fn.return_type))
    return with_calls(replace(fn, parameter_bindings=tuple(bindings),
                              record_fields=with_fields(declared, fields) if fields else declared,
                              return_representation=representation))


def repass(fn: MIRFunction, name: str, passing: ParamPassing) -> MIRFunction:
    """A forged passing: `fn` with the binding of its parameter slot `name` at `passing`."""
    names = {s.id: s.name for s in fn.slots}
    return replace(fn, parameter_bindings=tuple(replace(b, passing=passing) if names[sid] == name else b
                                                for sid, b in fn.slot_bindings.items()))


def reslot(slot: MIRSlot, **changes: object) -> MIRSlot:
    """`replace` for a slot, the fields of its shape (type, class, holding,
    access, members) included."""
    shape = {k: changes.pop(k) for k in list(changes) if k in _SHAPE_FIELDS}
    return replace(slot, shape=replace(slot.shape, **shape), **changes)


def leaf_shape(typ: TpyType, readonly: bool = False) -> MIRShape:
    """An inert leaf held by value."""
    return MIRShape(typ, MIRClass.LEAF, MIRHolding.VALUE, readonly)


def owned_shape(typ: TpyType, readonly: bool = False, members: tuple | None = (),
                cls: MIRClass | None = None) -> MIRShape:
    """Storage held here: an owned leaf, a record, a container with its members."""
    return MIRShape(typ, _class(typ, cls), MIRHolding.VALUE, readonly, members or ())


def borrowed_shape(typ: TpyType, readonly: bool = False, members: tuple | None = (),
                   cls: MIRClass | None = None) -> MIRShape:
    """A holder of storage elsewhere: a pointer (a borrowed record or
    container among them), or a container view's region."""
    found = _class(typ, cls)
    holding = MIRHolding.REGION if found is MIRClass.CONTAINER_VIEW else MIRHolding.POINTER
    return MIRShape(typ, found, holding, readonly, members or ())


def cursor_shape(typ: TpyType, readonly: bool = False, members: tuple | None = ()) -> MIRShape:
    """A native iterator over a container or a view of one."""
    return MIRShape(typ, _class(typ, None), MIRHolding.CURSOR, readonly, members or ())


def tuple_shape(typ: TpyType, members: tuple, readonly: bool = False) -> MIRShape:
    return MIRShape(typ, MIRClass.TUPLE, MIRHolding.VALUE, readonly, members)


def optional_shape(typ: TpyType, payload: MIRShape, readonly: bool = False) -> MIRShape:
    return MIRShape(typ, MIRClass.OPTIONAL, MIRHolding.VALUE, readonly, (None, payload))


def union_shape(typ: TpyType, members: tuple, readonly: bool = False) -> MIRShape:
    return MIRShape(typ, MIRClass.UNION, MIRHolding.VALUE, readonly, members)


def alias_shape(typ: TpyType, readonly: bool = False) -> MIRShape:
    """A narrowed alias of an inert-leaf payload."""
    return MIRShape(typ, MIRClass.LEAF, MIRHolding.ALIAS, readonly)


# The structural writes the interpreter runs for a native method stub, by
# method name: a test oracle for the source meaning, not a contract.
_CONTAINER_METHODS = {
    "append": lambda container, value: container.elements.append(value),
    "clear": lambda container: container.elements.clear(),
}


@dataclass(frozen=True)
class Reference:
    identity: int
    path: tuple[MIRFieldId | MIRTupleIndex, ...] = ()


@dataclass(eq=False)
class ContainerValue:
    """One container object, shared by every holder of it: element writes
    and structural writes (`append`, `clear`) are visible through each."""
    elements: list[int | Reference]

    def __post_init__(self) -> None:
        self.elements = list(self.elements)


@dataclass(frozen=True)
class IteratorValue:
    source: ContainerValue
    index: int = 0


@dataclass(frozen=True)
class TupleValue:
    elements: tuple[int | bool | Reference, ...]


@dataclass(frozen=True)
class UnionValue:
    """A sum's value: its selected alternative and that alternative's payload."""
    alternative: int
    payload: int | bool | Reference | None = None


def optional_value(payload: int | bool | Reference | None = None) -> UnionValue:
    """An Optional's value: the absent alternative 0, or 1 holding `payload`."""
    return UnionValue(0) if payload is None else UnionValue(1, payload)


@dataclass(frozen=True)
class PayloadAlias:
    place: MIRPlace


Value = int | bool | Reference | TupleValue | UnionValue | PayloadAlias | ContainerValue | IteratorValue
Record = dict[MIRFieldId | MIRTupleIndex, 'int | bool | Record']
Heap = dict[int, Record]

# Source-level meaning of the primitive operations a test body runs; a
# fixed-width result outside its type's range raises like a checked op.
_OPERATIONS = {
    "+": operator.add, "-": operator.sub, "*": operator.mul, "/": operator.truediv,
    "//": operator.floordiv, "%": operator.mod, "**": operator.pow, "<<": operator.lshift,
    ">>": operator.rshift, "&": operator.and_, "|": operator.or_, "^": operator.xor,
    "__neg__": operator.neg, "__pos__": operator.pos, "__invert__": operator.invert,
}


def execute(fn: MIRFunction, *args: Value, heap: Heap | None = None,
            global_state: dict[MIRGlobalId, int | bool] | None = None,
            output: list[tuple[Value, ...]] | None = None, begun: int | None = None,
            yields: list[Value] | None = None) -> Value | None:
    """Run `fn`; record storage it begins (a constructor's result) takes
    identity `begun` when given. A suspension hands its value to `yields`
    and resumes at once."""
    params = [s.id for s in fn.slots if s.kind is MIRSlotKind.PARAMETER]
    assert len(params) == len(args)
    values = dict(zip(params, args))
    regions = MIRRegionFlow(fn)
    objects = heap if heap is not None else {}
    blocks = {b.id: b for b in fn.blocks}
    slots = {s.id: s for s in fn.slots}
    physical: dict[MIRSlotId, Value] = {
        sid: value for sid, value in values.items()
        if slots[sid].shape.cls in (MIRClass.OPTIONAL, MIRClass.UNION)}
    global_values = global_state if global_state is not None else {}
    for slot in fn.slots:
        if slot.global_id is not None:
            assert slot.global_id in global_values, "global storage must be initialized by caller"
    records = fn.record_layouts
    next_identity = max(objects, default=0) + 1
    bid = fn.entry
    comparisons = {"<": operator.lt, "<=": operator.le, ">": operator.gt,
                   ">=": operator.ge, "==": operator.eq, "!=": operator.ne}
    # MIR names any element (`[elements]`); the lowering evaluates the index
    # into a fresh temporary just before the access, which the interpreter
    # takes as the element the source meant.
    last_index: list[int] = [0]

    def record(reference: Reference) -> Record:
        value = objects[reference.identity]
        for member in reference.path:
            value = value[member]
            assert isinstance(value, dict)
        return value

    def field(place: MIRPlace) -> tuple[Record, MIRFieldId]:
        reference = read(MIRPlace(place.root, place.projections[:-1]))
        assert isinstance(reference, Reference)
        member = place.projections[-1]
        assert isinstance(member, MIRField)
        return record(reference), member.id

    def read(place: MIRPlace) -> Value:
        identity = slots[place.root].global_id
        value = global_values[identity] if identity is not None else values[place.root]
        if isinstance(value, PayloadAlias):
            value = read(value.place)
        for projection in place.projections:
            match projection:
                case MIRUnionPayload():
                    assert isinstance(value, UnionValue) and value.alternative == projection.alternative
                    value = value.payload
                    assert value is not None
                case MIRTupleIndex():
                    assert isinstance(value, TupleValue)
                    value = value.elements[projection.index]
                case MIRDeref():
                    # A container value is shared by every holder: its own object.
                    assert isinstance(value, (Reference, ContainerValue))
                case MIRContainerElements():
                    assert isinstance(value, ContainerValue)
                    value = value.elements[last_index[0]]
                case _:
                    assert isinstance(projection, MIRField) and isinstance(value, Reference)
                    member = record(value)[projection.id]
                    value = (Reference(value.identity, value.path + (projection.id,))
                             if isinstance(member, dict) else member)
        return value

    for _ in range(100):
        block = blocks[bid]
        for stmt in block.statements:
            if isinstance(stmt, MIRCallStmt):
                callee = stmt.call.summary.callee
                method = _CONTAINER_METHODS.get(callee.identity.qualified_name.rsplit(".", 1)[-1])
                if not getattr(callee, "receiver", False) or method is None:
                    raise AssertionError("calls require callee execution, not summary evaluation")
                receiver, *arguments = (values[sid] for sid in stmt.call.arguments)
                method(receiver, *arguments)
                continue
            if isinstance(stmt, MIRPrint):
                printed = tuple(values[sid] for sid in stmt.arguments)
                if output is not None:
                    output.append(printed)
                continue
            if isinstance(stmt, MIRSuspend):
                if yields is not None:
                    yields.append(values[stmt.value])
                continue
            if isinstance(stmt, MIRStorageInit) and stmt.value is None:
                # Record storage begun: an object whose fields are written next.
                if begun is None:
                    begun, next_identity = next_identity, next_identity + 1
                objects[begun] = {}
                values[stmt.target.root] = Reference(begun)
                continue
            if isinstance(stmt, MIRStorageInit):
                physical[stmt.target.root] = UnionValue(stmt.alternative, stmt.value.value)
                values.pop(stmt.target.root, None)
                continue
            assert all(sid in values or slots[sid].global_id is not None for sid in statement_reads(stmt)), \
                "source read before assignment"
            if (isinstance(stmt.storage_write, MIRStorageWrite)
                    and stmt.storage_write.mode is MIRStorageWriteMode.IN_PLACE
                    and not stmt.target.projections and slots[stmt.target.root].shape.inline_sum):
                assert stmt.target.root in physical, "assignment before storage initialization"
            rhs = stmt.value
            match rhs:
                case MIRRangeAdvance():
                    value = values[rhs.source] + rhs.step
                    assert INT32_MIN <= value <= INT32_MAX, "range induction overflow"
                case MIRIteratorInit():
                    source = values[rhs.source]
                    assert isinstance(source, ContainerValue)
                    value = IteratorValue(source)
                case MIRIteratorHasNext():
                    source = values[rhs.source]
                    assert isinstance(source, IteratorValue)
                    value = source.index < len(source.source.elements)
                case MIRIteratorRead() | MIRIteratorAdvance():
                    source = values[rhs.source]
                    assert isinstance(source, IteratorValue) and source.index < len(source.source.elements)
                    value = (source.source.elements[source.index] if isinstance(rhs, MIRIteratorRead)
                             else IteratorValue(source.source, source.index + 1))
                case MIRConstant():
                    value = rhs.value
                case MIRRead():
                    value = read(rhs.source)
                case MIRAlias():
                    value = values[rhs.source]
                    assert isinstance(value, (Reference, ContainerValue))
                case MIRBorrow() if slots[stmt.target.root].shape.container_view_holder:
                    # A Span views the whole region of its source.
                    value = read(MIRPlace(rhs.source.root, rhs.source.projections[:-1]))
                    assert isinstance(value, ContainerValue)
                case MIRBorrow():
                    value = read(rhs.source)
                    assert isinstance(value, (Reference, ContainerValue))
                case MIRConstruct() if place_shape(stmt.target, slots, records).cls is MIRClass.TUPLE:
                    # The interpreter's tuple value: a storage member's record
                    # moves into the tuple's own backing object.
                    target = slots[stmt.target.root]
                    elements = []
                    backing: Record = {}
                    for i, (src, member) in enumerate(zip(rhs.fields, target.shape.elements)):
                        if member.is_storage:
                            backing[MIRTupleIndex(i)] = record(values[src]).copy()
                            elements.append(Reference(next_identity, (MIRTupleIndex(i),)))
                        else:
                            elements.append(values[src])
                    if target.shape.owned_tuple:
                        objects[next_identity] = backing
                        next_identity += 1
                    value = TupleValue(tuple(elements))
                case MIRCopy() if place_shape(stmt.target, slots, records).cls in WRAPPER_CLASSES:
                    # A wrapper copy keeps the selection and each member's value.
                    source = read(rhs.source)
                    match source:
                        case TupleValue():
                            value = TupleValue(source.elements)
                        case _:
                            assert isinstance(source, UnionValue)
                            value = UnionValue(source.alternative, source.payload)
                case MIRWrap():
                    payload = read(rhs.source) if rhs.source is not None else None
                    target_shape = place_shape(stmt.target, slots, records)
                    member = target_shape.alternatives[rhs.alternative]
                    if (isinstance(payload, Reference) and not stmt.target.projections
                            and member is not None and member.owns_storage):
                        # The temporary's record moves into the sum's own inline
                        # object, as a construct moves a tuple's record member;
                        # a refill of the same alternative keeps that object.
                        moved = record(payload).copy()
                        objects.pop(payload.identity, None)
                        current = physical.get(stmt.target.root)
                        if (isinstance(current, UnionValue) and current.alternative == rhs.alternative
                                and isinstance(current.payload, Reference)):
                            record(current.payload).clear()
                            record(current.payload).update(moved)
                            payload = current.payload
                        else:
                            objects[next_identity] = moved
                            payload = Reference(next_identity)
                            next_identity += 1
                    value = UnionValue(rhs.alternative, payload)
                case MIRIsAlternative():
                    source = read(rhs.source)
                    assert isinstance(source, UnionValue)
                    value = source.alternative in rhs.alternatives
                case MIRUnionExtract():
                    value = (PayloadAlias(rhs.source) if slots[stmt.target.root].shape.holding is MIRHolding.ALIAS
                             else read(rhs.source))
                case MIRConstruct() if place_shape(stmt.target, slots, records).cls is MIRClass.CONTAINER:
                    value = ContainerValue([values[src] for src in rhs.fields])
                case MIRCopy() | MIRMove():
                    source = rhs.source.root if isinstance(rhs, MIRCopy) else rhs.source
                    reference = values[source]
                    assert isinstance(reference, Reference)
                    value = record(reference).copy()
                case MIRCompare():
                    value = comparisons[rhs.op](values[rhs.left], values[rhs.right])
                case MIRNot():
                    value = not values[rhs.operand]
                case MIROp():
                    value = _OPERATIONS[rhs.op](*(values[sid] for sid in rhs.operands))
                    traits = int_traits_of(slots[stmt.target.root].type)
                    if traits is not None and not traits.min_value <= value <= traits.max_value:
                        raise OverflowError(f"{rhs.op} overflows {slots[stmt.target.root].type}")
                case MIRCall() if id(rhs.summary) in _HAND_CONSTRUCTORS:
                    value = {f.id: values[sid] for f, sid in zip(_HAND_CONSTRUCTORS[id(rhs.summary)].fields,
                                                                rhs.arguments)}
                case MIRCall() if id(rhs.summary) in _CONSTRUCTOR_BODIES:
                    # A construction runs its constructor's body over the same heap.
                    built = execute(_CONSTRUCTOR_BODIES[id(rhs.summary)],
                                    *(values[sid] for sid in rhs.arguments), heap=objects,
                                    global_state=global_values)
                    value = objects.pop(built.identity)
                    next_identity = max(objects, default=next_identity) + 1
                case _:
                    raise AssertionError(rhs)
            if isinstance(value, dict) and stmt.target.projections and isinstance(
                    stmt.target.projections[-1], MIRField):
                # An inline record member: the object around it keeps its identity.
                obj, member = field(stmt.target)
                if isinstance(obj.get(member), dict):
                    obj[member].clear()
                    obj[member].update(value)
                else:
                    obj[member] = value
            elif isinstance(value, dict):
                if stmt.target.projections:
                    reference = read(stmt.target)
                    assert isinstance(reference, Reference)
                else:
                    assert slots[stmt.target.root].shape.is_storage
                    if stmt.target.root in values:
                        # Validation admits repeated root writes only for reusable backing.
                        reference = values[stmt.target.root]
                        assert isinstance(reference, Reference)
                    else:
                        reference = Reference(next_identity)
                        next_identity += 1
                        values[stmt.target.root] = reference
                destination = record(reference) if stmt.target.projections else None
                if destination is not None:
                    if not builds_base(stmt):
                        # A base's constructor fills only the base's fields.
                        destination.clear()
                    destination.update(value)
                else:
                    objects[reference.identity] = value
            elif stmt.target.projections and isinstance(stmt.target.projections[-1], MIRContainerElements):
                container = read(MIRPlace(stmt.target.root, stmt.target.projections[:-1]))
                assert isinstance(container, ContainerValue)
                container.elements[last_index[0]] = value
            elif stmt.target.projections:
                obj, member = field(stmt.target)
                assert not isinstance(value, Reference)
                obj[member] = value
            else:
                identity = slots[stmt.target.root].global_id
                if identity is not None:
                    assert type(value) in (int, bool)
                    global_values[identity] = value
                else:
                    values[stmt.target.root] = value
                    if slots[stmt.target.root].shape.cls in (MIRClass.OPTIONAL, MIRClass.UNION):
                        physical[stmt.target.root] = value
                    if (slots[stmt.target.root].kind is MIRSlotKind.TEMPORARY and type(value) is int
                            and slots[stmt.target.root].shape.is_scalar):
                        last_index[0] = value
        term = block.terminator
        match term:
            case MIRReturn(value=result):
                return values[result] if result is not None else None
            case MIRBranch(condition=condition, then=then, otherwise=otherwise):
                edge = MIREdge(bid, int(not values[condition]))
                bid = then if values[condition] else otherwise
            case MIRGoto(target=target):
                edge = MIREdge(bid)
                bid = target
            case _:
                raise AssertionError(term)
        for sid in regions.edges[edge].reset:
            values.pop(sid, None)
        for sid in regions.edges[edge].ended:
            physical.pop(sid, None)
    raise AssertionError("unexpected nontermination")


def construct(fn: MIRFunction, identity: int, *args: Value, heap: Heap,
              global_state: dict[MIRGlobalId, int | bool] | None = None) -> None:
    """Run a constructor body, the record it builds and returns stored in
    `heap` at `identity`: the object later calls lend by that identity."""
    built = execute(fn, *args, heap=heap, global_state=global_state, begun=identity)
    assert built == Reference(identity)


def placed_storage(fn: MIRFunction) -> list[MIRSlot]:
    """The storage a body places, in slot order: a record a fill builds and
    moves into an owned sum is that sum's, not storage of its own."""
    moved = {stmt.value.source.root for block in fn.blocks for stmt in block.statements
             if isinstance(stmt, MIRAssign) and isinstance(stmt.value, MIRWrap) and stmt.value.source is not None
             and not stmt.target.projections and next(s for s in fn.slots if s.id == stmt.target.root).shape.owned_sum}
    return [s for s in fn.slots if s.shape.owns_storage and s.id not in moved]


def prepared(fn: MIRFunction) -> MIRPrepared | MIRNotCovered:
    """The body's one preparation (`prepare.prepare`): the input every pass
    takes, or the first dependency run's refusal."""
    return prepare(fn)


def workspace_functions(workspace: MIRCallWorkspace) -> dict[MIRBodyId, MIRFunction | MIRNotCovered]:
    """Each body the call workspace lowered, as its MIR function or its refusal."""
    return {body: lowered.function if isinstance(lowered, MIRLoweredStorage) else lowered
            for body, lowered in workspace.bodies.items()}
