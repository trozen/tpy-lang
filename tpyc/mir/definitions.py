"""Immutable, verified record certificates supplied by the THIR caller --
what storage of each record admits and the constructor a construction
calls -- and the builtin certificates of owned leaves' opaque storage."""

from collections.abc import Callable, Collection, Iterator, Mapping
from contextlib import nullcontext
from dataclasses import dataclass, field
from types import MappingProxyType

from ..compilation_context import activate_compiler, get_current_compiler
from ..thir import nodes as th
from ..thir.validate import _iter_children
from ..thir.scalar_leaves import (
    MIRClass, holds_loan, inline_member_type, loan_free, modeled_field, modeled_hierarchy,
    record_type, value_class,
    view_compatible,
)
from ..type_def_registry import ParamPassing, type_def_of
from ..typesys import (
    NominalType, OwnType, RecordInfo, TpyType, init_is_default_ctor, record_construction_fields, record_owner,
    return_representation, unwrap_readonly,
)
from .call_contract import MIRParameterBinding, param_type
from .coverage import MIRUnsupported, plain, require
from .value import (
    AGGREGATE_CLASSES, CONTAINER_CLASSES, WRAPPER_CLASSES, MIRField, MIRFieldId, MIRRecordLayout, MIRRecords, MIRShape,
    RecordFields, admitted_members, container_members, loan_field, member_types,
)


def view_members(layout: MIRRecordLayout | None) -> tuple[MIRField, ...]:
    """The view members of a record layout: each stores a loan the record
    object holds."""
    return () if layout is None else tuple(f for f in layout.fields if loan_field(f.type))


def owned_parameter(binding: MIRParameterBinding) -> bool:
    """A parameter holding an owned leaf lent (CONST_REF or VIEW: a readonly
    borrow of the caller's storage) or handed over (VALUE or OWN: the
    callee's own copy or the moved value); `parameter_binding` refuses any
    other passing."""
    return binding.cls is MIRClass.OWNED_LEAF


def owned_aggregate_parameter(binding: MIRParameterBinding) -> NominalType | None:
    """The record or container an `Own[...]` parameter is handed over at
    OWN: the callee's own storage, moved in by the caller. None for
    anything else."""
    return (binding.type if binding.cls in AGGREGATE_CLASSES and binding.storage
            and binding.passing is ParamPassing.OWN else None)


def optional_parameter(binding: MIRParameterBinding) -> MIRShape | None:
    """The value of an Optional parameter MIR models, at a constructor and
    a call alike: the caller's pointer at its payload record, or null; or a
    scalar Optional by value. None for anything else."""
    return binding.value if binding.cls is MIRClass.OPTIONAL else None


def optional_record_parameter(binding: MIRParameterBinding) -> NominalType | None:
    """The payload record of an Optional record parameter, else None."""
    value = optional_parameter(binding)
    return value.payload.type if value is not None and value.record_payload else None


def view_parameter(binding: MIRParameterBinding) -> bool:
    """A parameter holding a view of an owned leaf (`StrView`), passed by
    value: the caller's loan, which the callee holds."""
    return binding.cls is MIRClass.VIEW


def lends_view(binding: MIRParameterBinding, view: TpyType) -> bool:
    """Whether a parameter holds a loan a `view` member may store: a view
    parameter's own, or a borrow of an owned-leaf parameter the caller lends.
    A parameter the callee owns (by value or `Own[str]`) dies with the call."""
    return not binding.protocol and binding.value.leaf_borrow and view_compatible(view, binding.type)


def aggregate_parameter(binding: MIRParameterBinding) -> bool:
    """A constructor parameter holding a record or container: lent readonly
    at CONST_REF or handed over at OWN (`Own[T]`). One lent mutably has a
    writer the member initializer does not model."""
    return (binding.cls in AGGREGATE_CLASSES and not binding.storage and binding.passing is ParamPassing.CONST_REF
            and binding.readonly or owned_aggregate_parameter(binding) is not None)


def layout_copy_may_raise(layout: MIRRecordLayout,
                          layout_of: Callable[[NominalType], MIRRecordLayout | None]) -> bool:
    """Whether copying storage of `layout` can exit by exception: some owned
    leaf in it copies a buffer that may (`TypeDef.copy_may_raise`), a
    container field copies its elements into a new allocation, or a member
    record's copy may (`layout_of`, recursively). A member whose layout is
    unknown may."""
    if layout.opaque:
        return bool(type_def_of(layout.type).copy_may_raise)
    for f in layout.fields:
        bare = inline_member_type(f.type)
        cls = value_class(bare)
        if cls is MIRClass.OWNED_LEAF and type_def_of(bare).copy_may_raise:
            return True
        if cls in AGGREGATE_CLASSES:
            member = aggregate_facts(bare, layout_of)
            if member is None or member.copy_may_raise:
                return True
    return False


@dataclass(frozen=True)
class MIRAggregateFacts:
    """What storage of a record or container type admits beside its
    members: whether it copies, whether it moves, and whether its copy can
    exit by exception."""
    copyable: bool
    movable: bool
    copy_may_raise: bool


def aggregate_facts(typ: TpyType, layout_of: Callable[[NominalType], MIRRecordLayout | None]
                    ) -> MIRAggregateFacts | None:
    """The copy and move facts of record or container storage of type
    `typ`, one derivation for both (`layout_of` supplies the layouts): a
    record's from its layout (`layout_copy_may_raise`), a container's from
    its members' -- its copy is its elements' into a new allocation, its
    buffer changes hands on a move. None when a layout it needs is missing."""
    if value_class(typ) is MIRClass.CONTAINER:
        members = container_members(typ, False)
        if isinstance(members, str):
            return None
        layouts = [layout_of(m.type) for m in members if m.cls is MIRClass.RECORD]
        if any(layout is None for layout in layouts):
            return None
        return MIRAggregateFacts(all(layout.copyable for layout in layouts), True, True)
    layout = layout_of(typ)
    if layout is None:
        return None
    return MIRAggregateFacts(layout.copyable, layout.movable, layout_copy_may_raise(layout, layout_of))


def copy_may_raise(shape: MIRShape, facts: Callable[[TpyType], MIRAggregateFacts]) -> bool:
    """Whether copying a value of `shape` may raise: an owned leaf's
    buffer as its type declares, a record or container by its `facts`, a
    sum or tuple by the storage members it copies (an absent alternative,
    an inert scalar and a holder's loan copy no buffer)."""
    if shape.cls in WRAPPER_CLASSES:
        return any(copy_may_raise(m, facts) for m in shape.members if m is not None and m.is_storage)
    if shape.cls is MIRClass.OWNED_LEAF:
        return bool(type_def_of(shape.type).copy_may_raise)
    return facts(shape.type).copy_may_raise


@dataclass(frozen=True)
class MIRConstructorDefinition:
    """A record's certificate: its layout -- storage hook-free to build,
    copy, move and destroy, whose member records and container elements
    verify -- and the constructor a construction `R(...)` calls (`callee`):
    the record's own, or for a record constructing through its base's
    (`using Base::Base;`) the base's, whose result initializes the fields
    `initializes` declares in the record's storage."""
    constructor: th.THIRConstructor | th.THIRInheritedConstructor
    layout: MIRRecordLayout
    callee: th.THIRResolvedCallee
    initializes: NominalType | None = None
    # The constructor binds with no argument (none declared, or every one
    # defaulted), so C++ runs it for a base the source never calls.
    zero_argument: bool = False


# Verified definitions, or why a record has none, by record type: every one
# a record's definition reads -- its struct bases', its member records' and
# its container elements' -- at least.
Definitions = Mapping[NominalType, 'MIRConstructorDefinition | str']

_NO_DEFINITIONS: Definitions = MappingProxyType({})


def _member_definitions(ctor: th.THIRConstructor, layout: th.THIRRecordLayout, definitions: Definitions) -> None:
    """A record's storage is hook-free to build, copy, move and destroy only
    when each member's is: a member record's verified definition, and a
    container field's elements as a container holds them (`container_definition`)."""
    for f in layout.fields:
        bare = inline_member_type(f.type)
        cls = value_class(bare)
        if cls is MIRClass.RECORD:
            member = definitions.get(bare, "missing constructor definition")
            if isinstance(member, str):
                raise MIRUnsupported(ctor, f"member definition: {member}")
        # A container has no constructor: its elements verify as it holds them.
        elif cls is MIRClass.CONTAINER:
            container_definition(ctor, bare, lambda t: definitions.get(t, "missing constructor definition"))


def _record_identity(node: object, layout: object) -> None:
    """A THIR record layout MIR models: a record identity, its distinct
    struct-base ancestors, and its eligibility facts."""
    require(node, isinstance(layout, th.THIRRecordLayout), "missing record layout")
    typ = layout.type
    require(node, value_class(typ) is MIRClass.RECORD, "unsupported record identity")
    require(node, isinstance(layout.ancestors, tuple) and len(set(layout.ancestors)) == len(layout.ancestors)
            and all(isinstance(a, NominalType) and value_class(a) is MIRClass.RECORD and a != typ
                    for a in layout.ancestors),
            "unsupported record identity")
    require(node, all(type(v) is bool for v in (
        layout.unique_constructor, layout.custom_copy, layout.custom_move,
        layout.custom_destructor, layout.copyable, layout.movable)), "invalid record eligibility")


def _stored_loan_member(typ: TpyType) -> bool:
    """A member whose loans the record object stores at member keys: a view
    member, or an inline record member (its own view members, keyed under
    the member's place; its definition decides its fields)."""
    return value_class(typ) in (MIRClass.VIEW, MIRClass.RECORD)


def _record_members(node: object, layout: th.THIRRecordLayout) -> None:
    """Every field a modeled shape (`modeled_field`) keyed by an owner in
    the hierarchy. A view member stores its source's loan in the record
    object, and an inline record member's view members store theirs under
    the member's place; a container or wrapper member holding one would key
    it under an element or a payload, which no place models."""
    owners = (layout.type, *layout.ancestors)
    members = {MIRFieldId(f.owner, f.name) for f in layout.fields}
    require(node, not any(holds_loan(f.type) and not _stored_loan_member(f.type) for f in layout.fields),
            "record member holds a borrow")
    # `holds_loan` reads UNKNOWN as no loan; beside a stored loan that
    # unproved member (a native, protocol or `bytearray` field) could hold one too.
    require(node, not any(holds_loan(f.type) for f in layout.fields) or all(
        _stored_loan_member(f.type) or loan_free(f.type)
        for f in layout.fields), "record member loan unknown beside a view member")
    require(node, len(members) == len(layout.fields) and all(
        f.owner in owners and bool(f.name) and modeled_field(f.type)
        for f in layout.fields), "unsupported record fields")


def held_record_layout(source: th.THIRConstructor | th.THIRInheritedConstructor) -> MIRRecordLayout:
    """The layout a holder of the record reaches, decided from its fields
    alone: no construct, copy or destruction is admitted through it, so
    neither its initialization nor its special members matter."""
    layout = source.record_layout
    _record_identity(source, layout)
    _record_members(source, layout)
    return MIRRecordLayout(layout.type, tuple(MIRField(MIRFieldId(f.owner, f.name), f.type) for f in layout.fields),
                           layout.copyable, layout.movable, ancestors=layout.ancestors)


def _certificate(ctor: th.THIRConstructor, definitions: Definitions
                 ) -> tuple[MIRRecordLayout, dict[MIRFieldId, th.THIRFieldIdentity]]:
    """What storage of a constructor's record admits: a modeled identity
    built by its one constructor, no custom special member, modeled fields
    whose member records and container elements verify (from
    `definitions`), and parameters of published passing. Its layout and
    its fields by identity."""
    plain(ctor, {"record_name", "params", "mil_inits", "base_inits", "body", "record_layout", "temp_plan",
                 "storage_facts"})
    layout = ctor.record_layout
    _record_identity(ctor, layout)
    require(ctor, layout.unique_constructor, "constructor must be unique")
    require(ctor, not (layout.custom_copy or layout.custom_move or layout.custom_destructor),
            "custom record special member")
    members = {MIRFieldId(f.owner, f.name): f for f in layout.fields}
    _record_members(ctor, layout)
    _member_definitions(ctor, layout, definitions)
    require(ctor, len({p.name for p in ctor.params}) == len(ctor.params), "duplicate constructor parameter")
    # The constructor is a callable: callers bind each parameter at its published passing.
    for p in ctor.params:
        require(p, p.passing is not None, "unpublished parameter passing")
    fields = tuple(MIRField(MIRFieldId(f.owner, f.name), f.type) for f in layout.fields)
    return (MIRRecordLayout(layout.type, fields, layout.copyable, layout.movable, ancestors=layout.ancestors),
            members)


def _own_fields(ctor: th.THIRConstructor, typ: NominalType,
                members: Mapping[MIRFieldId, th.THIRFieldIdentity]) -> dict[MIRFieldId, th.THIRMilInit]:
    """The record's own fields its member-init list initializes, by
    identity, in the list's order: the ancestors' are its bases'."""
    own: dict[MIRFieldId, th.THIRMilInit] = {}
    for mil in ctor.mil_inits:
        plain(mil, {"field_cpp", "field_identity", "value"})
        fact = mil.field_identity
        require(ctor, isinstance(fact, th.THIRFieldIdentity) and fact.owner == typ
                and members.get(MIRFieldId(fact.owner, fact.name)) == fact, "constructor field identity")
        key = MIRFieldId(fact.owner, fact.name)
        require(ctor, key not in own, "duplicate constructor field")
        own[key] = mil
    return own


def constructor_callee(ctor: th.THIRConstructor) -> th.THIRResolvedCallee:
    """The callable a constructor is: a function of its parameters handing
    the caller its record by value (`Own[R]`), built where the caller
    receives it. Its identity is the record's, function-shaped (no owner:
    no receiver parameter)."""
    typ = ctor.record_layout.type
    result = OwnType(typ)
    passings = tuple(p.passing for p in ctor.params)
    signature = th.THIRCallableSignature(
        tuple(param_type(p) for p in ctor.params), result,
        passings=passings if all(p is not None for p in passings) else None,
        return_representation=return_representation(result))
    return th.THIRResolvedCallee(constructor_identity(typ), signature)


def constructor_identity(typ: NominalType) -> th.THIRFunctionIdentity:
    """The identity of record `typ`'s constructor: keyed by the record, not
    by a module (`constructor_record_qname` reads it back)."""
    return th.THIRFunctionIdentity(typ._module_qname or typ.name, "__init__")


def constructor_record_qname(identity: th.THIRFunctionIdentity) -> str:
    """The qualified name of the record whose constructor `identity` is
    (`constructor_identity`)."""
    return identity.module


def constructor_function(ctor: th.THIRConstructor) -> th.THIRFunction:
    """A constructor's declaration as the function it is: its parameters,
    its body, its `Own[R]` result and its callee (`constructor_callee`)."""
    callee = constructor_callee(ctor)
    return th.THIRFunction(f"{ctor.record_name}.__init__", ctor.params, callee.signature.return_type, ctor.body,
                           th.THIRFunctionLayout(), signature=callee.signature, resolved_callee=callee,
                           temp_plan=ctor.temp_plan, storage_facts=ctor.storage_facts)


@dataclass(frozen=True)
class MIRConstructorBody:
    """What a constructor's own body builds its record from: the record's
    certificate (`_certificate`), the own fields its member-init list
    initializes -- THIR's partition, in layout order -- and its base
    initializers, each a call of the base's constructor initializing the
    base's fields, with each base's certificate."""
    constructor: th.THIRConstructor
    layout: MIRRecordLayout
    members: tuple[th.THIRMilInit, ...]
    bases: tuple[tuple[th.THIRBaseInit, 'MIRConstructorDefinition'], ...]


def constructor_body(ctor: th.THIRConstructor, definitions: Definitions = _NO_DEFINITIONS) -> MIRConstructorBody:
    """A constructor whose member-init list and base initializers build
    every field before its body runs. A field THIR leaves to the body is
    default-constructed by the C++ skeleton first, which no fact publishes
    (`BUGS.md#ctor-demoted-field-default-construct-runs-init`), so such a
    constructor is refused. Every struct-base ancestor is built by exactly
    one base initializer (directly, or through the base it is an ancestor
    of)."""
    layout, members = _certificate(ctor, definitions)
    own = _own_fields(ctor, layout.type, members)
    built: set[NominalType] = set()
    initialized = set(own)
    bases: list[tuple[th.THIRBaseInit, MIRConstructorDefinition]] = []
    for bi in ctor.base_inits:
        plain(bi, {"base_cpp", "base", "args"})
        require(ctor, isinstance(bi.base, NominalType) and bi.base in layout.ancestors, "base constructor identity")
        base = definitions.get(bi.base, "missing constructor definition")
        if isinstance(base, str):
            raise MIRUnsupported(ctor, f"base definition: {base}")
        reached = {bi.base, *base.layout.ancestors}
        require(ctor, not reached & built, "base constructor called twice")
        built |= reached
        fields = {f.id for f in base.layout.fields}
        require(ctor, not fields & initialized, "duplicate constructor field")
        initialized |= fields
        bases.append((bi, base))
    require(ctor, built == set(layout.ancestors), "base constructor not called")
    require(ctor, initialized == set(members), "incomplete constructor initialization")
    # C++ initializes fields in layout order, whatever order the list is in.
    require(ctor, list(own) == [f.id for f in layout.fields if f.id in own], "constructor field order")
    return MIRConstructorBody(ctor, layout, tuple(own.values()), tuple(bases))


def _inherited(inherited: th.THIRInheritedConstructor, base: MIRConstructorDefinition) -> MIRConstructorDefinition:
    """A record constructing through its one struct base's constructor
    (`using Base::Base;`): a call of the base's constructor whose result
    initializes the record's fields, all the base's. It must add nothing
    the base's constructor does not build -- no own field, no other base,
    no own special member."""
    plain(inherited, {"record_layout", "base"})
    layout = inherited.record_layout
    require(inherited, isinstance(layout, th.THIRRecordLayout) and value_class(layout.type) is MIRClass.RECORD,
            "missing record layout")
    fields = tuple(MIRField(MIRFieldId(f.owner, f.name), f.type) for f in layout.fields)
    require(inherited, layout.ancestors == (inherited.base, *base.layout.ancestors)
            and fields == base.layout.fields
            and not (layout.custom_copy or layout.custom_move or layout.custom_destructor)
            and layout.copyable is base.layout.copyable and layout.movable is base.layout.movable,
            "inherited constructor shape")
    return MIRConstructorDefinition(inherited, MIRRecordLayout(
        layout.type, fields, layout.copyable, layout.movable, ancestors=layout.ancestors), base.callee,
        base.initializes or base.layout.type, base.zero_argument)


def _zero_argument(ctor: th.THIRConstructor) -> bool:
    """Whether a constructor binds with no argument. THIR publishes no
    parameter defaults, so a defaulted one is read off the record's own
    `__init__` (`init_is_default_ctor`, the fact codegen decides the C++
    default constructor by)."""
    if not ctor.params:
        return True
    record = _type_def_record(ctor.record_layout.type)
    return record is not None and init_is_default_ctor(record)


@dataclass(frozen=True)
class MIRContainerDefinition:
    """The builtin certificate of a native container's storage, from its
    TypeDef and type arguments: an opaque buffer whose members MIR types
    but never tracks one by one. `members` are its element (and a dict's
    value) at mutable access; a holder applies its own access
    (`value.with_access`). `records` are the definitions
    of its record members, which the body's layouts must carry."""
    members: tuple[MIRShape, ...]
    records: tuple['MIRConstructorDefinition', ...] = ()


@dataclass(frozen=True)
class MIROwnedLeafDefinition:
    """The builtin certificate of an owned leaf's storage (`TypeDef.owned_leaf`):
    one opaque buffer, copied, moved and destroyed with no hook. Kept apart
    from verified constructor definitions; nothing else earns it."""
    layout: MIRRecordLayout


def owned_leaf_layout(typ: NominalType) -> MIRRecordLayout:
    """An owned leaf's layout (`MIROwnedLeafDefinition`): its opaque buffer."""
    return MIRRecordLayout(typ, (), True, True, opaque=True)


@dataclass(frozen=True)
class MIRHeldLayout:
    """The layout of a record a body reaches only through a holder
    (`held_record_layout`), whether or not its definition verifies. Never a
    construct's, copy's or destruction's definition."""
    layout: MIRRecordLayout


def _type_def_record(typ: NominalType) -> RecordInfo | None:
    td = type_def_of(typ)
    return td.record if td is not None else None


class _DeclaredFields:
    """The fields the storage of a record whose hierarchy MIR models
    (`modeled_hierarchy`, the chain admission and the layouts' ancestors
    are read from) contains, each keyed by its declaring owner, resolved
    per record from its TypeDef under the compilation that registered it:
    the fields the one enumeration (`typesys.record_construction_fields`)
    lists, as it does for THIR's record layouts. A `RecordFields` lookup."""

    def __init__(self, compiler: object) -> None:
        self._compiler = compiler
        self._resolved: dict[TpyType, frozenset[MIRField] | None] = {}

    def _resolve(self, typ: TpyType) -> frozenset[MIRField] | None:
        if typ not in self._resolved:
            with activate_compiler(self._compiler) if self._compiler is not None else nullcontext():
                td = type_def_of(typ) if record_type(typ) else None
                record = td.record if td is not None else None
                self._resolved[typ] = None if record is None or modeled_hierarchy(record) is None else frozenset(
                    MIRField(MIRFieldId(record_owner(r), f.name), f.type)
                    for r, f in record_construction_fields(record, _type_def_record))
        return self._resolved[typ]

    def __getitem__(self, typ: TpyType, /) -> frozenset[MIRField]:
        found = self._resolve(typ)
        if found is None:
            raise KeyError(typ)
        return found

    def get(self, typ: TpyType, default: Collection[MIRField] | None = None, /) -> Collection[MIRField] | None:
        found = self._resolve(typ)
        return default if found is None else found


def declared_fields() -> RecordFields:
    """The declared fields of every record MIR models (`_DeclaredFields`),
    resolved under the active compilation (none: the static registry's
    records alone)."""
    return _DeclaredFields(get_current_compiler())


@dataclass(frozen=True, init=False)
class MIRDefinitions:
    """Index and check each actual emitted record's certificate once, including failures."""
    records: Mapping[NominalType, MIRConstructorDefinition | str]
    # The layout of every record whose fields MIR models (`held_record_layout`),
    # a record with no verified definition included, with the declared fields
    # of every record (`declared_fields`).
    layouts: MIRRecords
    # The compilation these were verified under, whose TypeDefs the
    # certificates and declared fields were read from (none: the static
    # registry's records alone).
    compiler: object = field(compare=False, repr=False)

    @property
    def fields(self) -> RecordFields:
        return self.layouts.fields

    def __init__(self, constructors: tuple[th.THIRConstructor, ...] = (), *,
                 inherited: tuple[th.THIRInheritedConstructor, ...] = ()) -> None:
        object.__setattr__(self, "compiler", get_current_compiler())
        sources: dict[NominalType, th.THIRConstructor | th.THIRInheritedConstructor | str] = {}
        for source in (*constructors, *inherited):
            if source.record_layout is None:
                continue
            typ = source.record_layout.type
            sources[typ] = "duplicate constructor definition" if typ in sources else source
        records: dict[NominalType, MIRConstructorDefinition | str] = {}
        layouts: dict[NominalType, MIRRecordLayout] = {}

        def verify(typ: NominalType) -> MIRConstructorDefinition | str:
            # Dependencies first: a certificate reads its bases', its member
            # records' and its container elements'. Inheritance is acyclic and diamond-free (sema) and
            # C++ forbids a record holding itself inline, so the recursion
            # ends; the placeholder only guards malformed input.
            if typ in records:
                return records[typ]
            source = sources.get(typ)
            if source is None:
                return "missing constructor definition"
            if isinstance(source, str):
                records[typ] = source
                return source
            records[typ] = "cyclic record definition"
            try:
                layouts[typ] = held_record_layout(source)
            except MIRUnsupported:
                pass
            try:
                if isinstance(source, th.THIRInheritedConstructor):
                    base = verify(source.base) if isinstance(source.base, NominalType) else "missing base"
                    if isinstance(base, str):
                        raise MIRUnsupported(source, f"base definition: {base}")
                    records[typ] = _inherited(source, base)
                else:
                    for read in _read_records(source):
                        verify(read)
                    records[typ] = MIRConstructorDefinition(source, _certificate(source, records)[0],
                                                            constructor_callee(source),
                                                            zero_argument=_zero_argument(source))
            except MIRUnsupported as failure:
                records[typ] = failure.reason
            return records[typ]

        for typ in sources:
            verify(typ)
        object.__setattr__(self, "records", MappingProxyType(records))
        object.__setattr__(self, "layouts", MIRRecords(MappingProxyType(layouts), _DeclaredFields(self.compiler)))

    def get(self, node: object, typ: NominalType
            ) -> MIRConstructorDefinition | MIROwnedLeafDefinition | MIRContainerDefinition:
        cls = value_class(typ)
        if cls is MIRClass.OWNED_LEAF:
            return MIROwnedLeafDefinition(owned_leaf_layout(typ))
        # A container's definition derives from its type, a record's from its verified constructor.
        if cls in CONTAINER_CLASSES:
            return self.container(node, typ)
        definition = self.records.get(typ, "missing constructor definition")
        if isinstance(definition, str):
            raise MIRUnsupported(node, definition)
        return definition

    def held_layout(self, node: object, typ: NominalType) -> MIRHeldLayout:
        """The layout a holder of record `typ` reaches (`layouts`), or the
        refusal of its definition."""
        layout = self.layouts.get(typ)
        if layout is None:
            definition = self.records.get(typ, "missing constructor definition")
            raise MIRUnsupported(node, definition if isinstance(definition, str) else "missing record layout")
        return MIRHeldLayout(layout)

    def container(self, node: object, typ: NominalType) -> MIRContainerDefinition:
        """A native container's or container view's members, or the refusal
        of its members (`container_definition`)."""
        return container_definition(node, typ, lambda t: self.records.get(t, "missing constructor definition"))


# A call a body makes through a callee's summary: a user function or method
# call, a construction `R(...)`, a constructor's base initializer.
Invocation = th.THIRCall | th.THIRMethodCall | th.THIRCtorCall | th.THIRBaseInit


def invocation_record(node: Invocation) -> NominalType | None:
    """The record a call builds through its constructor: a construction's
    own record, a base initializer's base; None for a plain call."""
    if isinstance(node, th.THIRBaseInit):
        return node.base
    if isinstance(node, th.THIRCtorCall):
        return unwrap_readonly(node.result_type)
    return None


def invocation_callee(node: Invocation, definitions: MIRDefinitions
                      ) -> tuple[th.THIRResolvedCallee | None, NominalType | None]:
    """The callee a call goes through, and the base whose fields its result
    initializes when it builds part of another record: a construction
    calls its record's constructor, a base initializer its base's; None
    for a record with no verified definition."""
    typ = invocation_record(node)
    if typ is not None:
        definition = definitions.records.get(typ)
        if not isinstance(definition, MIRConstructorDefinition):
            return None, None
        return definition.callee, definition.initializes or (typ if isinstance(node, th.THIRBaseInit) else None)
    return node.resolved_callee, None


def body_calls(fn: th.THIRFunction | th.THIRConstructor, definitions: MIRDefinitions
               ) -> Iterator[tuple[Invocation, th.THIRResolvedCallee]]:
    """Every call a body makes to a resolved user callee, with that callee
    (`invocation_callee`): calls, constructions, and a constructor's
    member-init values and base initializers."""
    # A base initializer is a plain cell the THIR walk only looks through, so
    # a constructor's walk starts from its parts, the base inits as calls.
    pending: list[object] = ([*fn.base_inits, *(m.value for m in fn.mil_inits), *fn.body]
                             if isinstance(fn, th.THIRConstructor) else list(_iter_children(fn)))
    while pending:
        node = pending.pop()
        if isinstance(node, (th.THIRCall, th.THIRMethodCall, th.THIRCtorCall, th.THIRBaseInit)):
            callee, _ = invocation_callee(node, definitions)
            if callee is not None:
                yield node, callee
        pending.extend(_iter_children(node))


def _read_records(ctor: th.THIRConstructor) -> list[NominalType]:
    """The records whose definitions `ctor`'s definition reads: its struct
    bases, its record fields, and its container fields' record members."""
    reads = [bi.base for bi in ctor.base_inits if isinstance(bi.base, NominalType)]
    for f in ctor.record_layout.fields:
        bare = inline_member_type(f.type)
        members = member_types(bare) if value_class(bare) is MIRClass.CONTAINER else None
        for typ in (bare,) if members is None else members:
            if value_class(typ) is MIRClass.RECORD:
                reads.append(unwrap_readonly(typ))
    return reads


def container_definition(node: object, typ: NominalType,
                         definition_of: Callable[[NominalType], MIRConstructorDefinition | str]
                         ) -> MIRContainerDefinition:
    """A native container's or container view's members (`container_members`;
    a view's own, at its own type arguments), a record member read through
    its verified definition (`definition_of`) whose layout the container
    admits (`admitted_members`), or the refusal of its members."""
    # The members' own refusal first: a definition is read only for a member MIR models.
    members = container_members(typ, False)
    if isinstance(members, str):
        raise MIRUnsupported(node, members)
    records: dict[NominalType, MIRConstructorDefinition] = {}
    layouts: dict[TpyType, MIRRecordLayout] = {}
    for member in members:
        if member.cls is MIRClass.RECORD:
            definition = definition_of(member.type)
            if isinstance(definition, str):
                raise MIRUnsupported(node, definition)
            records[member.type] = definition
            layouts[member.type] = definition.layout
        elif member.cls is MIRClass.OWNED_LEAF:
            layouts[member.type] = owned_leaf_layout(member.type)
    admitted = admitted_members(typ, False, layouts)
    require(node, not isinstance(admitted, str), admitted)
    return MIRContainerDefinition(admitted, tuple(records.values()))
