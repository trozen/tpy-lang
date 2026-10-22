"""The values MIR models, described once: a value's shape (its type,
class, holding, access and members), the record layouts a body supplies,
and the rules that build a shape -- one composer for every position
(`value_shape`), its holding decided once (`holding_of`), a native
container's members (`container_members`, with the layouts a body supplies
for them, `admitted_members`) -- the one check that a shape is one the
composer builds (`shape_problem`), and decide a value's access from the
source it comes from (`loan_access`).
A leaf: it reads THIR's nodes and value classes and the type system,
never another MIR module."""

from collections.abc import Collection, Iterator, Mapping
from dataclasses import dataclass, replace
from enum import Enum, auto
from types import MappingProxyType
from typing import Protocol

from ..thir import nodes as th
from ..thir.nodes import Form
from ..thir.scalar_leaves import (
    MIRClass, binds_cursor, declared_members, holds_loan, modeled_ancestors, modeled_field, modeled_members,
    native_container_subject, readonly_elements, record_type, value_class,
)
from ..type_def_registry import ParamPassing
from ..typesys import (
    NominalType, OwnType, ReadonlyType, Representation, TpyType, is_void_like_type, unwrap_readonly,
    unwrap_ref_type,
)


class MIRValidationError(ValueError):
    """A producer supplied malformed MIR, rather than unsupported source."""


class MIRHolding(Enum):
    """How a position holds its value, never derived from the type: a
    tuple's record member is owned or borrowed by the tuple's form, an
    Optional record payload is a pointer at a slot and inline at a field,
    an iterator has its container's type."""
    # Stored here: an inert leaf, owned storage, inline members.
    VALUE = auto()
    # A reference a `[deref]` follows: a borrowed record or container, an
    # owned leaf's borrow, a view holder.
    POINTER = auto()
    # A container view: a holder of another aggregate's `[elements]`
    # region, reached directly, never dereferenced.
    REGION = auto()
    # A native iterator over its container.
    CURSOR = auto()
    # A narrowed alias of a wrapper payload, naming the payload's storage.
    ALIAS = auto()


@dataclass(frozen=True)
class MIRShape:
    """One value as MIR models it: its type, its class (`value_class` of the
    type), how its position holds it, its access, and its members -- a
    tuple's elements, an Optional's payload, a union's alternatives (None
    for an absent one), a container's element and, for a dict, its value.
    A container member's access is the container's; element identity is
    never tracked: `[elements]` is one region every subscript, iterator and
    view of the container reaches."""
    type: TpyType
    cls: MIRClass
    holding: MIRHolding = MIRHolding.VALUE
    readonly: bool = False
    members: tuple['MIRShape | None', ...] = ()

    @property
    def payload(self) -> 'MIRShape':
        """An Optional's payload."""
        assert self.cls is MIRClass.OPTIONAL
        return self.members[0]

    @property
    def elements(self) -> 'tuple[MIRShape, ...]':
        """A tuple's members, in order."""
        assert self.cls is MIRClass.TUPLE
        return self.members

    @property
    def alternatives(self) -> 'tuple[MIRShape | None, ...]':
        """A union's members, None for an absent alternative."""
        assert self.cls is MIRClass.UNION
        return self.members

    @property
    def element(self) -> 'MIRShape':
        """What iterating a container yields: a list / set / Array element, a dict key."""
        assert self.cls in CONTAINER_CLASSES
        return self.members[0]

    @property
    def value(self) -> 'MIRShape | None':
        """A dict's value member, None for any other container."""
        assert self.cls in CONTAINER_CLASSES
        return self.members[1] if len(self.members) > 1 else None

    @property
    def subscript(self) -> 'MIRShape':
        """The member a subscript yields: a dict's value, else the element."""
        value = self.value
        return value if value is not None else self.element

    @property
    def is_scalar(self) -> bool:
        """An inert leaf held by value."""
        return self.cls is MIRClass.LEAF and self.holding is MIRHolding.VALUE

    @property
    def scalar_by_value(self) -> bool:
        """A member holding an inert leaf by value, with no access of its own."""
        return self.is_scalar and not self.readonly

    @property
    def is_storage(self) -> bool:
        """Owned storage held here: an owned leaf's buffer, a record, a
        container -- what a move empties while the holder lives on."""
        return self.holding is MIRHolding.VALUE and self.cls in _STORAGE_CLASSES

    @property
    def leaf_storage(self) -> bool:
        """An owned leaf's storage held here."""
        return self.holding is MIRHolding.VALUE and self.cls is MIRClass.OWNED_LEAF

    @property
    def is_holder(self) -> bool:
        """A holder of storage elsewhere -- a pointer, a region, an iterator's
        cursor, a payload alias: a loan of its own, whose root write points
        it elsewhere without writing storage another place reaches."""
        return self.holding is not MIRHolding.VALUE

    @property
    def owned_leaf_borrow(self) -> bool:
        """A readonly holder of an owned leaf's storage."""
        return self.holding is MIRHolding.POINTER and self.readonly and self.cls is MIRClass.OWNED_LEAF

    @property
    def view_holder(self) -> bool:
        """A readonly holder typed by a view over an owned leaf: its
        referents are the owned-leaf storage it views."""
        return self.holding is MIRHolding.POINTER and self.readonly and self.cls is MIRClass.VIEW

    @property
    def leaf_borrow(self) -> bool:
        """A holder an operation reads an owned leaf through: an owned leaf's
        own borrow or a view of one."""
        return self.owned_leaf_borrow or self.view_holder

    @property
    def container_view_holder(self) -> bool:
        """A holder typed by a container view (a Span, a dict view): it IS
        the elements region of the container it views, so its referents are
        `[elements]` places and a container projection on it is absorbed."""
        return self.cls is MIRClass.CONTAINER_VIEW and self.holding is MIRHolding.REGION

    @property
    def is_pointer(self) -> bool:
        """A reference one `[deref]` follows to the storage it points at."""
        return self.holding is MIRHolding.POINTER

    @property
    def borrow_holder(self) -> bool:
        """A holder of storage elsewhere that a lent argument, a borrowed
        result, an alias or a rebound holder is: a pointer, or a container
        view's region."""
        return self.holding in (MIRHolding.POINTER, MIRHolding.REGION)

    @property
    def borrowed_record(self) -> bool:
        """A pointer at a record's storage: how a record parameter, alias
        or borrowed result is held."""
        return self.cls is MIRClass.RECORD and self.is_pointer

    @property
    def borrowed_aggregate(self) -> bool:
        """A pointer at a record's or a container's storage: how an
        aggregate parameter, local holder or borrowed result is held."""
        return self.cls in AGGREGATE_CLASSES and self.is_pointer

    @property
    def owned_place(self) -> bool:
        """Storage at a place: owned storage, or the storage a view holder
        points at (its family's owned leaf, typed by the view)."""
        return self.holding is MIRHolding.VALUE and (self.is_storage or self.cls is MIRClass.VIEW)

    @property
    def whole_container(self) -> bool:
        """The object reached is a whole container: container storage or a
        borrow of one (an iterator is a cursor, not the object)."""
        return self.cls is MIRClass.CONTAINER and self.holding in (MIRHolding.VALUE, MIRHolding.POINTER)

    @property
    def container_or_view(self) -> bool:
        """What a whole-container operation (iteration, a builtin's read)
        takes: a whole container or a container view holder."""
        return self.whole_container or self.container_view_holder

    @property
    def owned_tuple(self) -> bool:
        """A tuple storing a record member inline."""
        return self.cls is MIRClass.TUPLE and any(m is not None and m.is_storage for m in self.members)

    @property
    def record_payload(self) -> bool:
        """An Optional whose payload is a pointer at a record."""
        return self.cls is MIRClass.OPTIONAL and lent_record(self) is not None

    @property
    def scalar_wrapper(self) -> bool:
        """An Optional or a union whose every payload is an inert leaf by value."""
        match self.cls:
            case MIRClass.OPTIONAL:
                return self.payload.scalar_by_value
            case MIRClass.UNION:
                return all(m is None or m.scalar_by_value for m in self.members)
        return False

    @property
    def form(self) -> Form:
        """The THIR form a holder of this value has: owned storage (and a
        tuple owning a record member) is STORAGE, any holder of storage
        elsewhere BORROW, anything held by value VALUE."""
        if self.holding is not MIRHolding.VALUE:
            return Form.BORROW
        if self.is_storage or self.owned_tuple:
            return Form.STORAGE
        return Form.VALUE


def lent_record(shape: MIRShape) -> MIRShape | None:
    """The pointer at the record a holder of `shape` lends: a record
    pointer itself, an Optional's pointer payload; else None (an inline
    payload is its holder's own storage, a scalar lends nothing)."""
    if shape.cls is MIRClass.OPTIONAL:
        shape = shape.payload
    return shape if shape is not None and shape.borrowed_record else None


_STORAGE_CLASSES = frozenset({MIRClass.OWNED_LEAF, MIRClass.RECORD, MIRClass.CONTAINER})
# Storage with member places of its own (an owned leaf's buffer has none),
# lent alike by a pointer at it; a source decides a loan's access.
AGGREGATE_CLASSES = _STORAGE_CLASSES - {MIRClass.OWNED_LEAF}
CONTAINER_CLASSES = frozenset({MIRClass.CONTAINER, MIRClass.CONTAINER_VIEW})
# A wrapper's or a container's members are the only ones a value carries.
MEMBERED_CLASSES = frozenset({MIRClass.TUPLE, MIRClass.OPTIONAL, MIRClass.UNION}) | CONTAINER_CLASSES


@dataclass(frozen=True)
class MIRFieldId:
    owner: NominalType
    name: str


@dataclass(frozen=True)
class MIRField:
    id: MIRFieldId
    type: TpyType


@dataclass(frozen=True)
class MIRRecordLayout:
    type: NominalType
    fields: tuple[MIRField, ...]
    copyable: bool
    movable: bool
    # An owned leaf's storage: a buffer MIR models whole, with no fields to
    # project (`MIRDefinitions` answers it from the TypeDef, never from a
    # constructor).
    opaque: bool = False
    # The struct-base ancestors, MRO order, nearest first: a field's owner
    # is `type` or one of them, and storage of `type` binds at any of them
    # (`call_contract.binds_at`).
    ancestors: tuple[NominalType, ...] = ()


Records = Mapping[TpyType, MIRRecordLayout]
class RecordFields(Protocol):
    """The fields record storage of each type contains, keyed by declaring
    owner (`definitions.declared_fields`). Looked up per record, never
    enumerated: which records exist is the compilation's, not MIR's."""

    def __getitem__(self, typ: TpyType, /) -> frozenset[MIRField]: ...

    def get(self, typ: TpyType, default: Collection[MIRField] = ..., /) -> Collection[MIRField] | None: ...


NO_FIELDS: RecordFields = MappingProxyType({})


class MIRRecords(Mapping[TpyType, MIRRecordLayout]):
    """What a walk of places reads (`shapes.step`): the record layouts a
    body or the definitions supply, by record type, and the declared fields
    of every record -- a layout is MIR's storage model of a record, which
    needs its verified definition; membership is a fact of every record."""

    def __init__(self, layouts: Records, fields: RecordFields, containers: Collection[TpyType] = ()) -> None:
        self._layouts = layouts
        self.fields = fields
        # The container types whose definitions were registered beside the layouts.
        self.containers = frozenset(containers)

    def __getitem__(self, typ: TpyType) -> MIRRecordLayout:
        return self._layouts[typ]

    def __iter__(self) -> Iterator[TpyType]:
        return iter(self._layouts)

    def __len__(self) -> int:
        return len(self._layouts)


_LEAVES = (MIRClass.LEAF, MIRClass.OWNED_LEAF)


def require_valid(condition: bool, message: str) -> None:
    if not condition:
        raise MIRValidationError(message)


def holding_representation(holding: MIRHolding) -> Representation:
    """The representation a value held this way has: stored here (or a
    payload alias naming such storage) it is its own storage, any other
    holder points elsewhere."""
    return Representation.STORAGE if holding in (MIRHolding.VALUE, MIRHolding.ALIAS) else Representation.REFERENCE


def with_access(members: tuple[MIRShape | None, ...], readonly: bool) -> tuple[MIRShape | None, ...]:
    """A holder's view of a container's members: a readonly holder reaches
    readonly members, stored so: `step` reads them as they are."""
    return tuple(m if m is None else replace(m, readonly=m.readonly or readonly) for m in members)


def member_types(typ: TpyType) -> tuple[TpyType, ...] | None:
    """The member types native container or container view `typ` declares
    (`declared_members`), access wrappers kept: its element, and a dict's
    value."""
    members = declared_members(typ)
    if members is None:
        return None
    element, value, _ = members
    return (element,) if value is None else (element, value)


def container_members(typ: TpyType, readonly: bool) -> tuple[MIRShape, ...] | str:
    """The members a holder of native container or container view `typ`
    reaches at access `readonly`, from the type alone; else the refusal.
    A member holds no borrow and is one MIR models (`modeled_members`: an
    inert or owned leaf, or the plain record element of a type with no
    value member). A member's access is its own wrapper's, a holder's on
    top (`with_access`); an inert leaf has none of its own. The layouts a
    body supplies for the members are `admitted_members`'."""
    members = member_types(typ)
    if members is None:
        return "unsupported native container type"
    # A stored borrow outlives the operation that put it there, which
    # needs the retention contracts MIR does not model yet.
    if any(holds_loan(m) for m in members):
        return "container holds a borrow"
    # A nested container or a record holding one has element places
    # beyond one hop.
    if not modeled_members(typ):
        return "unsupported native container element"
    shapes = []
    for member in members:
        bare = unwrap_readonly(member)
        cls = value_class(bare)
        shapes.append(value_shape(bare, cls, access=cls is not MIRClass.LEAF and isinstance(member, ReadonlyType)))
    return with_access(tuple(shapes), readonly)


def subject_class(declared: TpyType) -> MIRClass | None:
    """The value class of what a binding of type `declared` holds, its
    `readonly[...]` / `Own[...]` wrapper seen through; None for a type MIR
    does not model."""
    return value_class(native_container_subject(declared))


def admission_problem(subject: TpyType, cls: MIRClass | None) -> str | None:
    """Why MIR does not model an aggregate binding of type `subject` (a
    parameter's, a receiver's, a local's, a hoist's): a record whose
    hierarchy it does not model, a TypeDef missing included
    (`modeled_ancestors`), a container or container view whose members it
    does not (`container_members`); None when it does, and for any other
    class."""
    if cls is MIRClass.RECORD:
        return "unmodeled record" if modeled_ancestors(subject) is None else None
    if cls in CONTAINER_CLASSES:
        members = container_members(subject, False)
        return members if isinstance(members, str) else None
    return None


def admitted_members(typ: TpyType, readonly: bool, layouts: Records) -> tuple[MIRShape, ...] | str:
    """The members of native container or container view `typ` at access
    `readonly` (`container_members`) whose layouts `layouts` supplies;
    else the refusal: an owned leaf's layout is its opaque buffer, a
    record's is no opaque one and stores leaf fields only. The supplied
    layout is checked, not the declaration (`plain_record_element`): a
    layout a body supplies may differ."""
    members = container_members(typ, readonly)
    if isinstance(members, str):
        return members
    for member in members:
        layout = layouts.get(member.type)
        match member.cls:
            case MIRClass.OWNED_LEAF if layout is None or not layout.opaque:
                return "missing owned-leaf layout"
            case MIRClass.RECORD if layout is None:
                return "missing record layout"
            case MIRClass.RECORD if layout.opaque or not all(
                    not isinstance(f.type, ReadonlyType) and value_class(f.type) in _LEAVES for f in layout.fields):
                return "unsupported native container element"
    return members


class MIRRole(Enum):
    """What a position does with the value it holds: the one input of the
    value's holding (`holding_of`) besides its class."""
    # The position's own storage, or an inert value.
    STORAGE = auto()
    # A loan of storage elsewhere: a lent parameter, a borrowed result, a
    # holder of a place.
    LOAN = auto()
    # A narrowed alias of a wrapper payload, naming the payload's storage.
    ALIAS = auto()
    # A native iterator over its container.
    CURSOR = auto()


class MIRPosition(Enum):
    """Where a value is held, which places its members (`value_shape`).
    Field-ness is said here, not by a role: a field stores its value (its
    holding is storage's), but its position places its members."""
    # A parameter, a local, a temporary, a global, a call result.
    SLOT = auto()
    FIELD = auto()


WRAPPER_CLASSES = frozenset({MIRClass.TUPLE, MIRClass.OPTIONAL, MIRClass.UNION})
# The layout fact of each wrapper class.
_LAYOUT_CLASSES = {th.THIRTupleLayout: MIRClass.TUPLE, th.THIROptionalLayout: MIRClass.OPTIONAL,
                   th.THIRUnionLayout: MIRClass.UNION}
WrapperLayout = th.THIRTupleLayout | th.THIROptionalLayout | th.THIRUnionLayout


def holding_of(cls: MIRClass | None, role: MIRRole) -> MIRHolding:
    """How a position in `role` holds a value of class `cls` -- the one
    holding decision. A wrapper is held by value whatever its role: its
    members carry what it holds. A view is a loan whatever its role -- a
    leaf view a pointer, a container view the region it views -- and so is
    a protocol parameter (no class); a field stores anything else inline.
    Any other loan -- of a record, an owned leaf or a container alike -- is
    a pointer at the aggregate's storage."""
    if cls in WRAPPER_CLASSES:
        return MIRHolding.VALUE
    match role:
        case MIRRole.CURSOR:
            return MIRHolding.CURSOR
        case MIRRole.ALIAS:
            return MIRHolding.ALIAS
    if cls is MIRClass.CONTAINER_VIEW:
        return MIRHolding.REGION
    if cls in (MIRClass.VIEW, None) or role is MIRRole.LOAN:
        return MIRHolding.POINTER
    return MIRHolding.VALUE


def handed_type(declared: TpyType) -> bool:
    """Whether `declared` hands a value over (`Own[...]`, under any wrapper)."""
    return isinstance(unwrap_readonly(declared), OwnType)


def loan_field(typ: TpyType) -> bool:
    """Whether a record member of declared type `typ` is the loan the record
    holds -- a view member, read or rebound whole -- rather than a value the
    record stores inline. The one rule every reader of a field asks."""
    return value_class(typ) is MIRClass.VIEW


def position_problem(declared: TpyType, position: MIRPosition) -> str | None:
    """Whether `position` holds a value declared at `declared` at all: a
    field only one MIR models (`modeled_field`)."""
    if position is MIRPosition.FIELD and not modeled_field(declared):
        return "unsupported field type"
    return None


def value_shape(declared: TpyType, cls: MIRClass | None = None, *, access: bool = False,
                role: MIRRole = MIRRole.STORAGE, position: MIRPosition = MIRPosition.SLOT,
                facts: WrapperLayout | None = None, layouts: Records | None = None) -> MIRShape:
    """The one composer: the shape a value of type `declared` has where
    `position` holds it in `role` (`holding_of`), at access `access` --
    decided by `loan_access`, only placed here -- with its members. `cls`
    is the type's value class unless the caller knows the value as an
    inert leaf; a wrapper's comes from its layout fact `facts`, and its
    type is the bare one.

    The members, placed once:
    - a tuple, a union and an Optional keep the members their layout
      selects whatever their holding (`int32 | None` keeps its scalar
      payload); an Optional's own slot is storage at its own access, and
      a pointer payload carries the loan's (`readonly[P] | None`);
    - at a field an Optional stores its payload record inline, at the
      field's access (its holder's, and its declaration's);
    - a container or container view takes its members from the builder
      at its access (`container_members`; `with_access`), at a field those
      `layouts` admits (`admitted_members`), none when it admits none.
    A lent owned leaf and a view are a readonly pointer: the access their
    class has (`loan_access`). A THIR type's `Ref[...]` names no loan (the
    passing or the return representation does): it is stripped here, until
    THIR publishes bare types."""
    field = position is MIRPosition.FIELD
    if (problem := position_problem(declared, position)) is not None:
        raise MIRValidationError(problem)
    declared = unwrap_ref_type(declared)
    typ = declared
    if facts is not None:
        cls = _LAYOUT_CLASSES[type(facts)]
        typ = unwrap_readonly(declared)
    elif field:
        typ = unwrap_readonly(declared)
    if cls is None:
        cls = value_class(declared)
        require_valid(cls is not None, "unsupported slot type")
    members: tuple[MIRShape | None, ...] = ()
    # A slot's wrapper members are the ones its layout fact selects.
    require_valid(cls not in WRAPPER_CLASSES or facts is not None or field and cls is MIRClass.OPTIONAL,
                  "wrapper without its layout facts")
    match cls:
        case MIRClass.TUPLE:
            members = tuple(map(thir_member, facts.elements))
        case MIRClass.UNION:
            members = tuple(None if m is None else thir_member(m) for m in facts.elements)
        case MIRClass.OPTIONAL if field:
            # The payload lies in the field: it is reached at the field's access.
            members = (value_shape(typ.inner, MIRClass.RECORD,
                                   access=loan_access(declared, MIRClass.RECORD, Member(access))),)
        case MIRClass.OPTIONAL:
            members = (thir_member(facts.payload),)
            # Its own slot is storage; a loan's access is its payload's, in the layout.
            require_valid(not access, "readonly Optional slot")
        case MIRClass.CONTAINER | MIRClass.CONTAINER_VIEW if field:
            admitted = admitted_members(typ, access, layouts)
            members = () if isinstance(admitted, str) else admitted
        case MIRClass.CONTAINER | MIRClass.CONTAINER_VIEW:
            members = container_members(typ, access)
            # A slot's container is admitted before its shape is built.
            require_valid(not isinstance(members, str), "unadmitted container members")
    return MIRShape(typ, cls, holding_of(cls, role), access, members)


def thir_member(member: TpyType | th.THIRBorrowedRecord | th.THIROwnedRecord) -> MIRShape:
    """A wrapper member as THIR selected it: a borrowed record is a pointer
    at it, an owned record inline storage, any other member an inert leaf
    by value."""
    match member:
        case th.THIRBorrowedRecord():
            return value_shape(member.type, MIRClass.RECORD, access=member.readonly, role=MIRRole.LOAN)
        case th.THIROwnedRecord():
            return value_shape(member.type, MIRClass.RECORD, access=member.readonly)
    return value_shape(member, MIRClass.LEAF)


# The layout facts a THIR binding may carry, at most one, each in its own
# field: a wrapper's per-member forms, which its type does not state.
_LAYOUT_FACTS = {"optional_layout": th.THIROptionalLayout, "union_layout": th.THIRUnionLayout,
                 "tuple_layout": th.THIRTupleLayout}


def laid_value(declared: TpyType, fact: object) -> MIRShape | str:
    """The wrapper of `declared` THIR's layout fact `fact` lays out
    (`value_shape`), of THIR's schema -- a fact, its records at a definite
    access, a union's of the declared union -- unchecked against
    `declared`; else the refusal."""
    if type(fact) in _LAYOUT_CLASSES:
        records = (fact.payload,) if isinstance(fact, th.THIROptionalLayout) else fact.elements
    else:
        return "missing or invalid layout fact"
    if isinstance(fact, th.THIRUnionLayout) and fact.type != unwrap_readonly(declared):
        return "unsupported union layout"
    if any(isinstance(r, (th.THIRBorrowedRecord, th.THIROwnedRecord))
           and not (record_type(r.type) and type(r.readonly) is bool) for r in records):
        return "unsupported reference fact"
    return value_shape(declared, facts=fact)


def fact_shape(declared: TpyType, fact: object) -> MIRShape | str:
    """The value THIR's layout fact lays out (`laid_value`) read at
    `declared`, checked once (`shape_problem`); else the refusal."""
    shape = laid_value(declared, fact)
    return shape if isinstance(shape, str) else shape_problem(shape, declared) or shape


def lent_shape_problem(declared: TpyType, ref: object) -> str | None:
    """Whether the holder a borrowed record or container `ref` names (a
    call's borrowed result, a body's, a backing's record), one MIR models
    (`admission_problem`), is read at `declared` (`shape_problem`); None
    when it is, else the refusal."""
    cls = value_class(ref.type) if isinstance(ref, th.THIRBorrowedRecord) else None
    if not (cls in AGGREGATE_CLASSES and type(ref.readonly) is bool and admission_problem(ref.type, cls) is None):
        return "unsupported reference fact"
    return shape_problem(value_shape(ref.type, access=ref.readonly, role=MIRRole.LOAN), declared)


def layout_fact(node: object, *, others: tuple[str, ...] = ()) -> object:
    """The one layout fact `node` carries, in its class's field; None when
    it carries none, or carries one of the facts `others` names, which
    exclude a layout and are the caller's; else the refusal (a str: a
    fact never is one)."""
    present = [(name, f) for name in (*_LAYOUT_FACTS, *others) if (f := getattr(node, name, None)) is not None]
    if len(present) > 1:
        return "conflicting layout facts"
    if not present or present[0][0] in others:
        return None
    name, fact = present[0]
    return fact if isinstance(fact, _LAYOUT_FACTS[name]) else "missing or invalid layout fact"


def member_shape(typ: TpyType, holder_readonly: bool, layouts: Records) -> MIRShape:
    """The shape of a record's member of declared type `typ`, reached
    through a holder at access `holder_readonly`."""
    return value_shape(typ, access=loan_access(typ, value_class(typ), Member(holder_readonly)),
                       position=MIRPosition.FIELD, layouts=layouts)


# The classes a holder lends a value of: a record, a container (view), a
# view, an owned leaf, a tuple (itself) -- every holder form, not the
# aggregates alone.
_HOLDER_CLASSES = frozenset({MIRClass.RECORD, MIRClass.VIEW, MIRClass.OWNED_LEAF, MIRClass.TUPLE}) | CONTAINER_CLASSES


def holder_form(shape: MIRShape) -> MIRShape:
    """The shape of a holder lending a value of `shape`: its loan's holding
    (`holding_of`), at the value's access or the access its class's loan
    has (`loan_access`) -- a record pointer, a container's region, a
    view's or owned leaf's readonly holder, a tuple itself."""
    assert shape.cls in _HOLDER_CLASSES, f"no holder form of {shape.cls}"
    return replace(shape, holding=holding_of(shape.cls, MIRRole.LOAN),
                   readonly=shape.readonly or loan_access(shape.type, shape.cls, None))


def _class_problem(shape: MIRShape, representation: Representation) -> str | None:
    """A shape and each member typed by a bare type (its access is its
    `readonly` flag, a bool, never a wrapper) of the class it claims
    (`value_class` at the representation its holding gives it,
    `representation` for the shape itself), with members only on a wrapper
    or a container."""
    if type(shape.readonly) is not bool:
        return "invalid value access"
    if isinstance(shape.type, ReadonlyType):
        return "readonly-wrapped value type"
    if shape.cls is not value_class(shape.type, representation):
        return "value class mismatch"
    if shape.members and shape.cls not in MEMBERED_CLASSES:
        return "members on a memberless value"
    for member in shape.members:
        if member is not None and (problem := _class_problem(member, holding_representation(member.holding))):
            return problem
    return None


def _holdings(cls: MIRClass, declared: TpyType, position: MIRPosition) -> frozenset[MIRHolding]:
    """The holdings the composer gives a value of class `cls` declared at
    `declared` where `position` holds it (`holding_of` over the roles the
    position has): a field stores it, as an `Own[...]` declaration does;
    any other slot holds its storage, a loan of a record, owned leaf or
    container, a narrowed scalar payload's alias, or a container's cursor.
    (A read type's `Ref[...]` names no loan: a reference to storage reads
    the storage. Which passing lends is the parameter's contract.)"""
    if position is MIRPosition.FIELD or handed_type(declared):
        roles = {MIRRole.STORAGE}
    else:
        roles = {MIRRole.STORAGE}
        if cls in _STORAGE_CLASSES:
            roles.add(MIRRole.LOAN)
        if cls is MIRClass.LEAF:
            roles.add(MIRRole.ALIAS)
        if cls in CONTAINER_CLASSES:
            roles.add(MIRRole.CURSOR)
    return frozenset(holding_of(cls, role) for role in roles)


def _subject(shape: MIRShape, declared: TpyType) -> TpyType:
    """The type a shape of `declared` has: a wrapper's bare, a holder's the
    loaned type (an `Own[...]` declaration lends nothing), storage the
    owned one."""
    if shape.cls in WRAPPER_CLASSES or shape.is_holder:
        return unwrap_readonly(declared)
    return native_container_subject(declared)


def _member_problem(member: MIRShape, declared: TpyType, readonly: bool, wrapper: str,
                    holdings: tuple[MIRHolding, ...]) -> str | None:
    """A wrapper's member of declared type `declared` inside a wrapper
    declared readonly (`readonly`): a record held at one of `holdings`,
    that record, holding no borrow the wrapper would have to select, never
    with more access than the declaration grants; else an inert scalar of
    the declared type."""
    if member.cls is not MIRClass.RECORD:
        return None if member.scalar_by_value and member.type == unwrap_readonly(declared) else (
            f"unsupported {wrapper} scalar")
    if member.holding not in holdings:
        return f"unsupported {wrapper} record holding"
    if member.type != unwrap_readonly(declared):
        return "reference type mismatch"
    if holds_loan(member.type):
        return "wrapper holds a borrow"
    if not member.readonly and (readonly or isinstance(declared, ReadonlyType)):
        return f"{wrapper} layout increases access"
    return None


def _members_problem(shape: MIRShape, declared: TpyType, position: MIRPosition) -> str | None:
    """A value's members against the type it is declared at (`declared`):
    each wrapper member the declared one (a record pointed at or, in a
    tuple, stored; an inert scalar of exactly the declared type, an
    Optional's of its declared payload's), a container's the builder's."""
    readonly = isinstance(declared, ReadonlyType)
    match shape.cls:
        case MIRClass.TUPLE:
            types = shape.type.element_types
            if len(shape.members) != len(types):
                return "tuple layout arity"
            for member, typ in zip(shape.members, types):
                if member is None:
                    return "invalid tuple element"
                if problem := _member_problem(member, typ, readonly, "tuple", (MIRHolding.VALUE, MIRHolding.POINTER)):
                    return problem
        case MIRClass.UNION:
            types = shape.type.members
            if len(shape.members) != len(types) or len({unwrap_readonly(m) for m in types}) != len(types):
                return "unsupported union layout"
            kinds = set()
            for member, typ in zip(shape.members, types):
                if member is None:
                    problem = None if is_void_like_type(typ) else "union absence type mismatch"
                else:
                    problem = _member_problem(member, typ, readonly, "union", (MIRHolding.POINTER,))
                if problem is not None:
                    return problem
                if member is not None:
                    kinds.add((member.cls, member.holding))
            if len(kinds) != 1 or len(shape.members) < 2:
                return "mixed or empty union layout"
        case MIRClass.OPTIONAL:
            # At a slot the payload record is a pointer, at a field inline.
            holding = MIRHolding.VALUE if position is MIRPosition.FIELD else MIRHolding.POINTER
            if shape.type.force_pointer_repr or len(shape.members) != 1 or shape.payload is None:
                return "unsupported optional layout"
            if position is MIRPosition.FIELD and shape.payload.cls is not MIRClass.RECORD:
                return "unsupported optional payload"
            # An inline payload is reached at its field's access, as a container's members are.
            return _member_problem(shape.payload, shape.type.inner, readonly or shape.readonly, "optional", (holding,))
        case MIRClass.CONTAINER | MIRClass.CONTAINER_VIEW:
            members = container_members(shape.type, shape.readonly)
            if isinstance(members, str):
                return members
            # A field's container exposes no members its layouts do not admit.
            if shape.members != members and not (position is MIRPosition.FIELD and shape.members == ()):
                return "container members disagree with its type"
            if shape.holding is MIRHolding.CURSOR and not binds_cursor(shape.type):
                return "iterator binds no element"
    return None


def member_compatible(source: MIRShape, target: MIRShape) -> bool:
    """Whether a wrapper member of shape `source` copies into, or binds,
    one of shape `target` -- the member-types rule `shape_problem` checks
    against a declaration, here between two shapes: the same value held
    the same way, never gaining access."""
    return (source.type == target.type and source.cls is target.cls and source.holding is target.holding
            and source.members == target.members and (not source.readonly or target.readonly))


def shape_problem(shape: MIRShape, declared: TpyType, position: MIRPosition = MIRPosition.SLOT,
                  representation: Representation | None = None) -> str | None:
    """Whether `shape` is one the composer (`value_shape`) builds for a
    value declared at `declared` where `position` holds it -- the one
    intrinsic shape check; None when it is. Its class the type's at the
    representation its holder gives it (`representation`, its holding's
    unless a parameter's passing states it), a holding the composer gives
    that class there (`holding_of`), at a slot a holder never more mutable
    than the access its class or declaration gives every loan of it and a
    wrapper at exactly its own (`loan_access`, no source: a lent owned
    leaf, a view, a payload alias and a holder declared `readonly[...]`
    read only; a wrapper, a container's or owned leaf's storage is its own
    mutable storage), a field one MIR
    models, its members the declared type's, none more mutable than it,
    none holding a borrow. What the holder's context decides -- placement,
    durations, registered layouts, passings -- is its caller's."""
    if representation is None:
        representation = holding_representation(shape.holding)
    if (problem := position_problem(declared, position)) is not None:
        return problem
    if (problem := _class_problem(shape, representation)) is not None:
        return problem
    if shape.type != _subject(shape, declared):
        return "reference type mismatch" if shape.cls in AGGREGATE_CLASSES else "value type mismatch"
    if shape.holding not in _holdings(shape.cls, declared, position):
        return "invalid holding"
    # A field's member is at its holder's access (`Member`), composed by `member_shape`.
    if position is MIRPosition.SLOT:
        if shape.cls in WRAPPER_CLASSES:
            if shape.readonly is not loan_access(declared, shape.cls, None):
                return "readonly wrapper slot"
        elif shape.is_holder and not shape.readonly and loan_access(
                declared, shape.cls, Local(alias=True) if shape.holding is MIRHolding.ALIAS else None):
            return "mutable loan of a readonly value"
        # A slot's own container or owned-leaf buffer is written in place
        # (grown, assigned, moved from); readonly is a loan's, never storage's.
        elif shape.is_storage and shape.readonly:
            return "readonly owned storage"
    return _members_problem(shape, declared, position)


@dataclass(frozen=True)
class Passing:
    """A parameter lent at its passing convention (`passing`): a record or
    container lent at CONST_REF reads only, at MUT_REF writes."""
    passing: ParamPassing


@dataclass(frozen=True)
class Declared:
    """An access a declaration states: a stub's parameter, a container
    view's, a call result's (readonly too where it borrows a receiver
    bound readonly: its caller folds that in)."""
    readonly: bool


@dataclass(frozen=True)
class Published:
    """An access THIR decided and publishes only inside a fact: an Optional
    record parameter's payload (`P | None` at POINTER is readonly where the
    body only reads it, mutable where it writes through it: one passing,
    two accesses), a hoisted record binding's, a global's (its binding
    says whether any use writes it)."""
    readonly: bool


@dataclass(frozen=True)
class Local:
    """A local's access inputs: its declaration bound const (a loop
    variable sema made const, `const_loop_var`); what it is bound over
    readonly (a loop variable over a readonly source: `auto&&` deduces
    const); a narrowed payload alias."""
    const: bool = False
    source_readonly: bool = False
    alias: bool = False


@dataclass(frozen=True)
class Member:
    """A value a record stores, reached through its holder: at the
    holder's access (`holder_readonly`), readonly too where its declaration
    wraps it `readonly[...]`."""
    holder_readonly: bool


AccessSource = Passing | Declared | Published | Local | Member
# The classes a loan of which only reads: an owned leaf lent, a view, a
# protocol parameter (no class). A tuple, a union and storage keep the
# access their members carry.
_READ_ONLY_LOANS = frozenset({MIRClass.OWNED_LEAF, MIRClass.VIEW, None})
# The classes a `readonly[...]` declaration makes a readonly loan of
# whatever its source.
_DECLARED_READONLY_CLASSES = AGGREGATE_CLASSES | {MIRClass.CONTAINER_VIEW}


def loan_access(declared: TpyType, cls: MIRClass | None, source: AccessSource | None) -> bool:
    """Whether a loan of a value of class `cls`, declared at `declared`,
    is readonly, from the source its access comes from; None for the
    class's own (`_READ_ONLY_LOANS`, a container view of readonly
    elements): what the class and the declaration alone give. The one site
    deciding an access: a record or container declared `readonly[...]` is
    readonly whatever its source; a container view is readonly too when
    its elements are. The callee's own storage lends nothing: its binding has no loan to ask
    about."""
    bare = unwrap_readonly(declared)
    if cls in _DECLARED_READONLY_CLASSES and isinstance(declared, ReadonlyType):
        return True
    match source:
        case Passing(passing=passing):
            if cls in AGGREGATE_CLASSES:
                return passing is ParamPassing.CONST_REF
        case Declared(readonly=readonly):
            if cls in AGGREGATE_CLASSES:
                return readonly
            if cls is MIRClass.CONTAINER_VIEW and readonly:
                return True
        case Published(readonly=readonly):
            return readonly
        case Local(const=const, source_readonly=source_readonly, alias=alias):
            return const or source_readonly or alias
        case Member(holder_readonly=holder_readonly):
            return holder_readonly or isinstance(declared, ReadonlyType)
    return cls in _READ_ONLY_LOANS or cls is MIRClass.CONTAINER_VIEW and readonly_elements(bare)
