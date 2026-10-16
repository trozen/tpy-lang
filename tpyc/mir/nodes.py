"""Immutable MIR with body-scoped holders and logical storage projections."""

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from functools import cached_property
from types import MappingProxyType
from enum import Enum, auto

from ..parse import SourceLocation
from ..thir.nodes import Form, THIRBorrowedRecord, THIRFunction
from ..thir.scalar_leaves import MIRClass
from ..type_def_registry import ParamPassing
from ..typesys import NominalType, TpyType
from .call_contract import MIRCallSummary, MIRContainerElements, MIRContainerStructure, MIRGlobalId


class MIRValidationError(ValueError):
    """A producer supplied malformed MIR, rather than unsupported source."""


@dataclass(frozen=True)
class MIRBodyId:
    module: str
    declaration: str


class MIRBodyKind(Enum):
    FREE_FUNCTION = auto()
    METHOD = auto()
    CONSTRUCTOR = auto()
    MODULE = auto()
    CLOSURE = auto()
    GENERATOR = auto()
    ASYNC = auto()
    GENERIC = auto()


def function_body_kind(fn: THIRFunction) -> MIRBodyKind:
    # THIR withholds the receiver fact wherever `self` is not an ordinary
    # borrowed receiver (staticmethods, properties, dunders, consuming
    # methods), so having an owner record never makes a body a METHOD.
    return MIRBodyKind.METHOD if fn.receiver is not None else MIRBodyKind.FREE_FUNCTION


@dataclass(frozen=True)
class MIRSlotId:
    body: MIRBodyId
    index: int


@dataclass(frozen=True)
class MIRBlockId:
    body: MIRBodyId
    index: int


@dataclass(frozen=True)
class MIRRegionId:
    body: MIRBodyId
    index: int


@dataclass(frozen=True)
class MIRRegion:
    id: MIRRegionId
    parent: MIRRegionId | None
    entry: MIRBlockId


@dataclass(frozen=True)
class MIREdge:
    source: MIRBlockId
    # Goto/return use arm 0; branches use 0 for true and 1 for false.
    arm: int = 0


@dataclass(frozen=True)
class MIRPoint:
    block: MIRBlockId
    index: int


class MIRSlotKind(Enum):
    PARAMETER = auto()
    GLOBAL = auto()
    LOCAL = auto()
    TEMPORARY = auto()


class MIRHolding(Enum):
    """How a position holds its value, never derived from the type: a
    tuple's record member is owned or borrowed by the tuple's form, an
    Optional record payload is a pointer at a slot and inline at a field,
    an iterator has its container's type."""
    # Stored here: an inert leaf, owned storage, inline members.
    VALUE = auto()
    # A reference a `[deref]` follows: a borrowed record, an owned leaf's
    # borrow, a view holder.
    POINTER = auto()
    # A holder whose `[elements]` region is reached directly, never
    # dereferenced: a borrowed container, a container view.
    REGION = auto()
    # A native iterator over its container.
    CURSOR = auto()
    # A narrowed alias of a wrapper payload, naming the payload's storage.
    ALIAS = auto()


class MIRStorageDuration(Enum):
    BODY = auto()
    CALLER = auto()


class MIRRecordStorageKind(Enum):
    DIRECT = auto()
    OPTIONAL = auto()


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
    def container_storage(self) -> bool:
        """A native container's storage held here."""
        return self.holding is MIRHolding.VALUE and self.cls is MIRClass.CONTAINER

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
        return self.holding is MIRHolding.REGION and self.cls is MIRClass.CONTAINER_VIEW

    @property
    def is_pointer(self) -> bool:
        """A reference one `[deref]` follows to the storage it points at."""
        return self.holding is MIRHolding.POINTER

    @property
    def borrowed(self) -> bool:
        """A holder of storage elsewhere that a lent argument, a borrowed
        result or a rebound holder is: a pointer, or a container view's region."""
        return self.is_pointer or self.container_view_holder

    @property
    def borrow_holder(self) -> bool:
        """A holder a borrow fills -- a borrowed result, an alias: a pointer
        or a region (a borrowed container, a container view)."""
        return self.holding in (MIRHolding.POINTER, MIRHolding.REGION)

    @property
    def borrowed_container(self) -> bool:
        """A whole container held elsewhere: a borrowed container's region."""
        return self.whole_container and not self.container_storage

    @property
    def owned_place(self) -> bool:
        """Storage at a place: owned storage, or the storage a view holder
        points at (its family's owned leaf, typed by the view)."""
        return self.holding is MIRHolding.VALUE and (self.is_storage or self.cls is MIRClass.VIEW)

    @property
    def whole_container(self) -> bool:
        """A whole container object: a borrowed container or container
        storage (an iterator is a cursor, not the object)."""
        return self.cls is MIRClass.CONTAINER and self.holding in (MIRHolding.VALUE, MIRHolding.REGION)

    @property
    def region(self) -> bool:
        """A value whose `[elements]` region a place reaches directly:
        container storage, a borrowed container, or a view of one."""
        return self.cls in CONTAINER_CLASSES and self.holding in (MIRHolding.VALUE, MIRHolding.REGION)

    @property
    def owned_tuple(self) -> bool:
        """A tuple storing a record member inline."""
        return self.cls is MIRClass.TUPLE and any(m is not None and m.is_storage for m in self.members)

    @property
    def record_payload(self) -> bool:
        """An Optional whose payload is a pointer at a record."""
        return self.cls is MIRClass.OPTIONAL and self.payload.holding is MIRHolding.POINTER

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


_STORAGE_CLASSES = frozenset({MIRClass.OWNED_LEAF, MIRClass.RECORD, MIRClass.CONTAINER})
CONTAINER_CLASSES = frozenset({MIRClass.CONTAINER, MIRClass.CONTAINER_VIEW})
# A wrapper's or a container's members are the only ones a value carries.
MEMBERED_CLASSES = frozenset({MIRClass.TUPLE, MIRClass.OPTIONAL, MIRClass.UNION}) | CONTAINER_CLASSES


@dataclass(frozen=True)
class MIRSlot:
    id: MIRSlotId
    shape: MIRShape
    kind: MIRSlotKind
    name: str | None = None
    alias_source: 'MIRPlace | None' = None
    global_id: MIRGlobalId | None = None
    storage_duration: MIRStorageDuration | MIRRegionId | None = None
    residence: MIRRegionId | None = None
    record_storage: MIRRecordStorageKind = MIRRecordStorageKind.DIRECT
    # How the signature passes a PARAMETER slot (`THIRParam.passing`);
    # None elsewhere, and a parameter without it is no scalar leaf.
    passing: ParamPassing | None = None

    @property
    def type(self) -> TpyType:
        return self.shape.type

    @property
    def readonly(self) -> bool:
        return self.shape.readonly

    @property
    def form(self) -> Form:
        return self.shape.form


@dataclass(frozen=True)
class MIRDeref:
    pass


@dataclass(frozen=True)
class MIRTupleIndex:
    index: int


@dataclass(frozen=True)
class MIROptionalPayload:
    pass


@dataclass(frozen=True)
class MIRUnionPayload:
    alternative: int


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


@dataclass(frozen=True)
class MIRPlace:
    root: MIRSlotId
    projections: tuple[MIRDeref | MIRField | MIRTupleIndex | MIROptionalPayload | MIRUnionPayload
                       | MIRContainerStructure | MIRContainerElements, ...] = ()


@dataclass(frozen=True)
class MIRConstant:
    # The Python value of a scalar leaf constant (`scalar_leaves.leaf_constant`)
    # or of an owned leaf's (`scalar_leaves.owned_constant`). Written to owned
    # storage it is materialized there; written to a borrowed holder it is a
    # literal in static storage, which the holder borrows.
    value: int | bool | float | str | bytes


@dataclass(frozen=True)
class MIRCall:
    summary: MIRCallSummary
    arguments: tuple[MIRSlotId, ...]
    # The call can exit by exception: the consumed summary's
    # `not normal_return_only`, so the caller's own exit fact sees it.
    may_raise: bool = False
    # Where the call expression is, and whether its receiver is bound
    # (`h.m(x)`, not `H.m(h, x)`): what a diagnostic needs to find the
    # analyzed call and its argument for each parameter. No MIR fact.
    site: SourceLocation | None = field(default=None, compare=False)
    bound_receiver: bool = field(default=False, compare=False)
    # The parameters whose argument is an empty Optional built for the call
    # (`MIROptionalConstruct()`): the callee reaches nothing through them,
    # so their summarized effects name no caller storage.
    absent: frozenset[int] = frozenset()

    @property
    def present_effects(self) -> MIRCallSummary:
        """The summary as the caller sees it: an absent argument reaches no
        caller storage, so no write, stored loan, return origin or no-alias
        obligation through it names any. Every consumer of a built call's
        effects (dependencies, retention, `coverage.call_write_places`)
        reads this, never `summary`'s effect sets. Lowering's admission
        check (`lower._Lowering`) runs before the call is built and reads
        the raw summary, so a writing callee given a literal None still
        counts as writing for its evaluation-order rules (TODO.md)."""
        absent = self.absent
        if not absent:
            return self.summary
        summary = self.summary
        return replace(
            summary,
            writes=frozenset(w for w in summary.writes if w.parameter not in absent),
            returns=frozenset(o for o in summary.returns if o.parameter not in absent),
            transfers=frozenset(t for t in summary.transfers if t.holder not in absent and t.source not in absent),
            obligations=frozenset(o for o in summary.obligations
                                  if o.parameter not in absent and o.written.parameter not in absent))


@dataclass(frozen=True)
class MIRRead:
    source: MIRPlace
    # An element access that can exit by exception (an index out of range,
    # a missing key); only a `[elements]` source can.
    may_raise: bool = False


@dataclass(frozen=True)
class MIRAlias:
    source: MIRSlotId


@dataclass(frozen=True)
class MIRBorrow:
    source: MIRPlace
    # As `MIRRead.may_raise`: a subscript borrowing one element may raise,
    # a slice or view borrowing the whole region does not.
    may_raise: bool = False


@dataclass(frozen=True)
class MIRConstruct:
    """Build a record from one operand per field: an inert leaf by value, an
    owned leaf copied out of the storage a borrowed holder lends (the
    constructor's member initializer copies it) or moved out of owned
    temporary storage the call hands over, or a view member storing the loan
    a lending holder holds. Into a container layout it is a literal: one
    operand per element (a dict's keys and values alternate), each by its
    member's form."""
    fields: tuple[MIRSlotId, ...]
    # Some owned-leaf member is copied, and that copy can exit by exception
    # (`TypeDef.copy_may_raise`); a container literal allocates, so always.
    may_raise: bool = False


@dataclass(frozen=True)
class MIRTupleConstruct:
    elements: tuple[MIRSlotId | MIRConstruct, ...]


@dataclass(frozen=True)
class MIRTupleCopy:
    source: MIRSlotId


@dataclass(frozen=True)
class MIROptionalConstruct:
    # The payload operand; None builds an empty wrapper.
    source: MIRPlace | None = None


@dataclass(frozen=True)
class MIROptionalCopy:
    source: MIRPlace
    # Copying an engaged payload into inline payload storage (an Optional
    # record field) copies the record, which may raise like any record copy
    # (`layout_copy_may_raise`); a borrowed or scalar payload copies no buffer.
    may_raise: bool = False


@dataclass(frozen=True)
class MIRIsPresent:
    source: MIRPlace


@dataclass(frozen=True)
class MIRUnionConstruct:
    alternative: int
    # The payload operand; None selects an absent alternative.
    source: MIRPlace | None = None


@dataclass(frozen=True)
class MIRUnionCopy:
    source: MIRPlace


@dataclass(frozen=True)
class MIRIsAlternative:
    source: MIRPlace
    alternatives: tuple[int, ...]


@dataclass(frozen=True)
class MIRUnionExtract:
    source: MIRPlace


@dataclass(frozen=True)
class MIRCopy:
    source: MIRPlace
    # The copy can exit by exception: a buffer allocation a bare `except:`
    # catches (an owned leaf's `TypeDef.copy_may_raise`; for a record, any
    # buffer in its whole layout, `layout_copy_may_raise`).
    may_raise: bool = False


@dataclass(frozen=True)
class MIRMove:
    source: MIRSlotId


@dataclass(frozen=True)
class MIRCompare:
    op: str
    left: MIRSlotId
    right: MIRSlotId


@dataclass(frozen=True)
class MIRNot:
    operand: MIRSlotId


@dataclass(frozen=True)
class MIROp:
    """A certified primitive operation (`THIRBinOp.certified_op`,
    `THIRUnaryArith.certified_op`, `THIRSubscript.certified_op`,
    `THIRCoerce.certified_conversion`): it reads its operands -- inert leaves
    by value, owned leaves through borrowed holders live until it runs --
    retains nothing, and yields an inert leaf or a fresh owned value. `op`
    names it for inspection only. `may_raise`: it can exit by exception
    (checked overflow, a zero divisor, an index out of range), which the
    body's `exceptional_exits` records."""
    op: str
    operands: tuple[MIRSlotId, ...]
    may_raise: bool


@dataclass(frozen=True)
class MIRIteratorInit:
    source: MIRSlotId


@dataclass(frozen=True)
class MIRIteratorHasNext:
    source: MIRSlotId


@dataclass(frozen=True)
class MIRIteratorRead:
    source: MIRSlotId


@dataclass(frozen=True)
class MIRIteratorAdvance:
    source: MIRSlotId


@dataclass(frozen=True)
class MIRRangeAdvance:
    """Unit induction after a successful bounded int32 range iteration."""
    source: MIRSlotId
    step: int


MIRRvalue = (MIRConstant | MIRCall | MIRRead | MIRCompare | MIRNot | MIROp | MIRAlias | MIRBorrow
             | MIRConstruct | MIRCopy | MIRMove | MIRTupleConstruct | MIRTupleCopy
             | MIROptionalConstruct | MIROptionalCopy | MIRIsPresent
             | MIRUnionConstruct | MIRUnionCopy | MIRIsAlternative | MIRUnionExtract
             | MIRIteratorInit | MIRIteratorHasNext | MIRIteratorRead | MIRIteratorAdvance | MIRRangeAdvance)


class MIRRecordWriteMode(Enum):
    INITIALIZE_ONCE = auto()
    INITIALIZE_REGION = auto()
    OWN_SITE = auto()
    IN_PLACE = auto()
    OPTIONAL_ASSIGN = auto()


@dataclass(frozen=True)
class MIRRecordWrite:
    mode: MIRRecordWriteMode
    rebind_owner: MIRSlotId | None = None


class MIRPayloadWriteMode(Enum):
    INITIALIZE = auto()
    INITIALIZE_REGION = auto()
    ASSIGN = auto()


@dataclass(frozen=True)
class MIRPayloadWrite:
    mode: MIRPayloadWriteMode


@dataclass(frozen=True)
class MIRTupleInitialization:
    """Initialize inline members together, once per backing activation."""


@dataclass(frozen=True)
class MIRAssign:
    target: MIRPlace
    value: MIRRvalue
    loc: SourceLocation | None = None
    storage_write: MIRRecordWrite | MIRPayloadWrite | MIRTupleInitialization | None = None


@dataclass(frozen=True)
class MIRStorageInit:
    """Construct a physical wrapper without assigning its source binding."""
    target: MIRPlace
    alternative: int
    value: MIRConstant
    loc: SourceLocation | None = None


@dataclass(frozen=True)
class MIRRecordStorageInit:
    """Construct an empty backing wrapper, without constructing a record."""
    target: MIRPlace
    loc: SourceLocation | None = None


@dataclass(frozen=True)
class MIRCallStmt:
    call: MIRCall
    loc: SourceLocation | None = None


@dataclass(frozen=True)
class MIRPrint:
    """Write inert leaves to standard output through the runtime's scalar
    formatter: a read of each argument, and an effect outside every
    parameter-rooted summary."""
    arguments: tuple[MIRSlotId, ...]
    loc: SourceLocation | None = None


MIRStatement = MIRAssign | MIRStorageInit | MIRRecordStorageInit | MIRCallStmt | MIRPrint


def statement_target(stmt: MIRStatement) -> MIRPlace | None:
    match stmt:
        case MIRAssign(target=target) | MIRStorageInit(target=target) | MIRRecordStorageInit(target=target):
            return target
        case MIRCallStmt() | MIRPrint():
            return None
        case _:
            raise TypeError("unknown MIR statement")


def statement_call(stmt: MIRStatement) -> MIRCall | None:
    match stmt:
        case MIRAssign(value=MIRCall() as call) | MIRCallStmt(call=call):
            return call
        case MIRAssign() | MIRStorageInit() | MIRRecordStorageInit() | MIRPrint():
            return None
        case _:
            raise TypeError("unknown MIR statement")


@dataclass(frozen=True)
class MIRGoto:
    target: MIRBlockId
    loc: SourceLocation | None = None


@dataclass(frozen=True)
class MIRBranch:
    condition: MIRSlotId
    then: MIRBlockId
    otherwise: MIRBlockId
    loc: SourceLocation | None = None


@dataclass(frozen=True)
class MIRReturn:
    value: MIRSlotId | None = None
    loc: SourceLocation | None = None


MIRTerminator = MIRGoto | MIRBranch | MIRReturn


@dataclass(frozen=True)
class MIRBlock:
    id: MIRBlockId
    statements: tuple[MIRStatement, ...]
    terminator: MIRTerminator
    region: MIRRegionId | None = None


class MIRMemberInitMode(Enum):
    # An inert leaf, by value; an Optional record member left empty.
    SCALAR = auto()
    # An owned leaf copied from a parameter's storage (borrowed or owned) or
    # materialized from a constant: an allocation. A record copied through
    # the record parameter lent readonly: its whole storage, which may
    # allocate (`definitions.layout_copy_may_raise`).
    COPY = auto()
    # An owned leaf moved out of a by-value parameter's storage; a record
    # moved out of an `Own[R]` parameter's storage, or built by its own
    # constructor (`MIRMemberInits`) and moved in.
    MOVE = auto()
    # A view member storing the loan its source holds: a view parameter's,
    # an owned-leaf parameter's lent storage, or a literal's static storage.
    # No buffer is copied, so it never raises.
    BORROW = auto()


@dataclass(frozen=True)
class MIRMemberInits:
    """A record member built by its own constructor at entry: initialized
    exactly as the receiver is, one initializer per field of the member's
    layout, in layout order. There are no temporaries before the CFG, so
    each field names its own parameter or constant."""
    fields: tuple['MIRMemberInit', ...]


@dataclass(frozen=True)
class MIRMemberInit:
    """How one member of the receiver is initialized at entry, in layout order.
    A container member is copied from a container parameter, or MOVEd from
    the literal its `MIRConstruct` builds over parameters; a record member
    built by its own constructor is MOVEd from its `MIRMemberInits`. An
    Optional record member is left empty (`MIROptionalConstruct()`), COPYs
    the payload an Optional record parameter lends, or MOVEs a payload its
    own constructor builds (`MIRMemberInits`)."""
    source: MIRSlotId | MIRConstant | MIRConstruct | MIRMemberInits | MIROptionalConstruct
    mode: MIRMemberInitMode = MIRMemberInitMode.SCALAR
    # The initialization can exit by exception (`TypeDef.copy_may_raise` of a
    # COPY, a record copy's layout, a literal's allocation, some field of a
    # `MIRMemberInits`).
    may_raise: bool = False
    loc: SourceLocation | None = None


@dataclass(frozen=True)
class MIRReceiverInit:
    """Initialize supplied storage before the CFG can observe the receiver."""
    receiver: MIRSlotId
    fields: tuple[MIRMemberInit, ...]


def member_init_operands(members: tuple[MIRMemberInit, ...]) -> tuple[MIRSlotId, ...]:
    """The slots entry initialization reads, through literals and nested
    member initializers."""
    operands: list[MIRSlotId] = []
    for member in members:
        match member.source:
            case MIRSlotId():
                operands.append(member.source)
            case MIRConstruct(fields=fields):
                operands.extend(fields)
            case MIRMemberInits(fields=nested):
                operands.extend(member_init_operands(nested))
    return tuple(operands)


@dataclass(frozen=True)
class MIRFunction:
    id: MIRBodyId
    return_type: TpyType
    slots: tuple[MIRSlot, ...]
    blocks: tuple[MIRBlock, ...]
    entry: MIRBlockId
    records: tuple[MIRRecordLayout, ...] = ()
    receiver_init: MIRReceiverInit | None = None
    kind: MIRBodyKind = MIRBodyKind.FREE_FUNCTION
    regions: tuple[MIRRegion, ...] = ()
    call_summaries: tuple[MIRCallSummary, ...] = ()
    borrowed_result: THIRBorrowedRecord | None = None
    # Some statement can exit the body by exception (`validate.statement_may_raise`):
    # a raising operation, an allocating copy or materialization, a call whose
    # summary may raise, or a print; or a receiver member's initialization
    # copies an owned leaf. Derived at lowering, re-checked by the validator.
    exceptional_exits: bool = False

    @cached_property
    def record_layouts(self) -> 'Mapping[TpyType, MIRRecordLayout]':
        """The body's record layouts by record type."""
        return MappingProxyType({r.type: r for r in self.records})


@dataclass(frozen=True)
class MIRNotCovered:
    body: MIRBodyId
    node_kind: str
    reason: str
    loc: SourceLocation | None = None
    # The slot the reason names (`%N`), when it names one.
    slot: 'MIRSlotId | None' = None


def source_order(loc: SourceLocation | None) -> tuple[int, int, int]:
    """Where a location stands in the source, a missing one last: what a
    pick among several candidates orders by, so no slot or block number
    decides what the user sees."""
    return (0, loc.line, loc.column) if loc is not None else (1, 0, 0)
