"""A bounded MIR interpreter with explicit shared object identities."""

from collections.abc import Callable, Collection, Iterable
import copy
from dataclasses import dataclass, replace
import operator
from types import MappingProxyType

from ..compilation_context import activate_compiler
from ..type_def_registry import TypeCategory, TypeDef, _type_defs, int_traits_of, register
from ..typesys import INT32_MIN, INT32_MAX, FieldInfo, NominalType, RecordInfo

from .nodes import (
    MIRSlot, MIRAlias, MIRBranch, MIRCallStmt, MIRCompare, MIRConstant, MIRFunction, MIRGoto, MIRNot, MIRPlace, MIRRead,
    MIRReturn, MIRSlotKind, MIRGlobalId, MIRBorrow, MIRConstruct, MIRCopy, MIRMemberInits, MIRMove, MIRSlotId,
    MIRPayloadWrite, MIRPayloadWriteMode, MIRRecordStorageInit, MIRRecordStorageKind, MIRTupleConstruct, MIRTupleCopy,
    MIRStorageInit, MIREdge, MIRIsPresent, MIROptionalConstruct, MIROptionalCopy, MIRUnionConstruct, MIRUnionCopy,
    MIRIsAlternative, MIRUnionExtract, MIRIteratorInit, MIRIteratorHasNext, MIRIteratorRead, MIRIteratorAdvance,
    MIRRangeAdvance, MIROp, MIRPrint, place_shape,
)
from .value import (
    NO_FIELDS, Declared, MIRField, MIRFieldId, MIRHolding, MIRRecordLayout, MIRRecords, MIRShape, RecordFields,
)
from .region_flow import MIRRegionFlow
from ..thir import nodes as th
from ..thir.scalar_leaves import MIRClass, value_class
from ..type_def_registry import ParamPassing
from ..typesys import OwnType, TpyType, return_representation
from .call_contract import FUNCTION, MIRParameterBinding, parameter_binding
from .definitions import MIRDefinitions, declared_fields
from .shapes import MIRContainerElements, MIRDeref, MIROptionalPayload, MIRTupleIndex, MIRUnionPayload
from .validate import statement_reads

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
    return replace(fn, parameter_bindings=tuple(bindings),
                   record_fields=with_fields(declared, fields) if fields else declared,
                   return_representation=representation)


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
    return MIRShape(typ, MIRClass.OPTIONAL, MIRHolding.VALUE, readonly, (payload,))


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
class OptionalValue:
    payload: int | bool | Reference | None = None


@dataclass(frozen=True)
class UnionValue:
    alternative: int
    payload: int | bool | Reference | None = None


@dataclass(frozen=True)
class PayloadAlias:
    place: MIRPlace


Value = int | bool | Reference | TupleValue | OptionalValue | UnionValue | PayloadAlias | ContainerValue | IteratorValue
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
            output: list[tuple[Value, ...]] | None = None) -> Value | None:
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
    if fn.receiver_init is not None:
        init = fn.receiver_init
        reference = values[init.receiver]
        assert isinstance(reference, Reference) and not reference.path
        assert reference.identity not in objects or not objects[reference.identity]
        def member_value(member: MIRField, source: MIRSlotId | MIRConstant | MIRConstruct | MIRMemberInits) -> Value:
            # A record member is inline storage: a copy of what its parameter
            # holds (or the parameter's own storage moved in), or the fields
            # its own constructor initializes.
            layout = records.get(member.type)
            layout = None if layout is None or layout.opaque else layout
            match source:
                case MIRConstant():
                    return source.value
                case MIRMemberInits():
                    return {f.id: member_value(f, m.source) for f, m in zip(layout.fields, source.fields)}
                case MIRConstruct():
                    return ContainerValue([s.value if isinstance(s, MIRConstant) else values[s]
                                           for s in source.fields])
            value = values[source]
            if layout is not None and isinstance(value, Reference):
                held = objects[value.identity]
                for step in value.path:
                    held = held[step]
                return copy.deepcopy(held)
            # A container member copies its parameter's container.
            return ContainerValue(value.elements) if isinstance(value, ContainerValue) else value
        objects[reference.identity] = {
            member.id: member_value(member, value.source)
            for member, value in zip(records[slots[init.receiver].type].fields, init.fields)
        }
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
                case MIROptionalPayload():
                    assert isinstance(value, OptionalValue) and value.payload is not None
                    value = value.payload
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
            if isinstance(stmt, MIRRecordStorageInit):
                physical[stmt.target.root] = OptionalValue()
                values.pop(stmt.target.root, None)
                continue
            if isinstance(stmt, MIRStorageInit):
                physical[stmt.target.root] = (OptionalValue() if slots[stmt.target.root].shape.cls is MIRClass.OPTIONAL
                                              else UnionValue(stmt.alternative, stmt.value.value))
                values.pop(stmt.target.root, None)
                continue
            assert all(sid in values or slots[sid].global_id is not None for sid in statement_reads(stmt)), \
                "source read before assignment"
            if isinstance(stmt.storage_write, MIRPayloadWrite) and stmt.storage_write.mode is MIRPayloadWriteMode.ASSIGN:
                assert stmt.target.root in physical, "assignment before storage initialization"
            rhs = stmt.value
            if (not stmt.target.projections
                    and slots[stmt.target.root].record_storage is MIRRecordStorageKind.OPTIONAL):
                assert stmt.target.root in physical, "record assignment before wrapper initialization"
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
                case MIRTupleConstruct():
                    target = slots[stmt.target.root]
                    elements = []
                    backing: Record = {}
                    for i, src in enumerate(rhs.elements):
                        if isinstance(src, MIRConstruct):
                            layout = records[place_shape(stmt.target, slots, records).elements[i].type]
                            backing[MIRTupleIndex(i)] = {f.id: values[s] for f, s in zip(layout.fields, src.fields)}
                            elements.append(Reference(next_identity, (MIRTupleIndex(i),)))
                        else:
                            elements.append(values[src])
                    if target.shape.owned_tuple:
                        objects[next_identity] = backing
                        next_identity += 1
                    value = TupleValue(tuple(elements))
                case MIRTupleCopy():
                    source = values[rhs.source]
                    assert isinstance(source, TupleValue)
                    value = TupleValue(source.elements)
                case MIROptionalConstruct():
                    value = OptionalValue(read(rhs.source) if rhs.source is not None else None)
                case MIROptionalCopy():
                    source = read(rhs.source)
                    assert isinstance(source, OptionalValue)
                    value = OptionalValue(source.payload)
                case MIRIsPresent():
                    source = read(rhs.source)
                    assert isinstance(source, OptionalValue)
                    value = source.payload is not None
                case MIRUnionConstruct():
                    value = UnionValue(rhs.alternative, read(rhs.source) if rhs.source is not None else None)
                case MIRUnionCopy():
                    source = read(rhs.source)
                    assert isinstance(source, UnionValue)
                    value = UnionValue(source.alternative, source.payload)
                case MIRIsAlternative():
                    source = read(rhs.source)
                    assert isinstance(source, UnionValue)
                    value = source.alternative in rhs.alternatives
                case MIRUnionExtract():
                    value = (PayloadAlias(rhs.source) if slots[stmt.target.root].shape.holding is MIRHolding.ALIAS
                             else read(rhs.source))
                case MIRConstruct() if place_shape(stmt.target, slots, records).cls is MIRClass.CONTAINER:
                    value = ContainerValue([values[src] for src in rhs.fields])
                case MIRConstruct():
                    layout = records[place_shape(stmt.target, slots, records).type]
                    value = {f.id: values[src] for f, src in zip(layout.fields, rhs.fields)}
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
                case _:
                    raise AssertionError(rhs)
            if isinstance(value, dict):
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
