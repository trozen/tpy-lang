"""Immutable MIR with body-scoped holders and logical storage projections."""

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from functools import cached_property
from types import MappingProxyType
from enum import Enum, auto

from ..parse import SourceLocation
from ..thir.nodes import Form, THIRBorrowedRecord, THIRFunction
from ..typesys import Representation, TpyType
from .call_contract import MIRCallSummary, MIRGlobalId, MIRParameterBinding, path_projections
from .shapes import (
    MIRUnionPayload, MIRWalk, Projection, lent_prefix, walk, walk_to,
)
from .value import (
    NO_FIELDS, MIRField, MIRRecordLayout, MIRRecords, MIRShape, MIRValidationError, RecordFields, loan_field,
)


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


class MIRStorageDuration(Enum):
    BODY = auto()
    CALLER = auto()


class MIRRecordStorageKind(Enum):
    DIRECT = auto()
    OPTIONAL = auto()


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
class MIRPlace:
    root: MIRSlotId
    projections: tuple[Projection, ...] = ()


def place_walk(place: MIRPlace, slots: Mapping[MIRSlotId, MIRSlot], records: MIRRecords) -> MIRWalk:
    """`place`'s projections stepped from its root slot's shape."""
    return walk(slots[place.root].shape, place.projections, records)


def place_shape(place: MIRPlace, slots: Mapping[MIRSlotId, MIRSlot], records: MIRRecords) -> MIRShape:
    """The value `place` denotes."""
    return walk_to(slots[place.root].shape, place.projections, records)


def view_member(place: MIRPlace) -> MIRField | None:
    """The view member a place ends at -- the loan a record object stores,
    read or rebound whole -- or None."""
    last = place.projections[-1] if place.projections else None
    return last if isinstance(last, MIRField) and loan_field(last.type) else None


def under_object(place: MIRPlace, slots: Mapping[MIRSlotId, MIRSlot]) -> tuple[Projection, ...] | None:
    """The projections of `place` under the object its root slot lends --
    behind a pointer or a wrapper's pointer member (`lent_prefix`) -- or
    None when the place does not reach through one."""
    prefix = lent_prefix(slots[place.root].shape, place.projections)
    return None if prefix is None else place.projections[len(prefix):]


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


@dataclass(frozen=True)
class MIRPresentCall:
    """A call as presence sees it at its point (`presence.present_call`):
    its effects less what an argument proven absent reaches
    (`present_effects`), and the caller's places those effects write
    (`coverage.call_write_places`) -- the one resolved write list every
    reader of a call's effects takes."""
    call: 'MIRCall'
    effects: MIRCallSummary
    writes: tuple['MIRPlace', ...]


def present_effects(summary: MIRCallSummary, excluded: Mapping[int, frozenset[int]]) -> MIRCallSummary:
    """A call's summary as the caller sees it: a sum argument's alternative
    presence proves unselected (`excluded`, by parameter,
    `presence.present_call`) lends no caller object, so no write, stored
    loan, return origin or no-alias obligation through it -- a path whose
    first selector names it -- names any."""
    if not excluded:
        return summary

    def live(parameter: object, path: tuple[object, ...]) -> bool:
        return not (path and isinstance(path[0], MIRUnionPayload)
                    and path[0].alternative in excluded.get(parameter, frozenset()))
    return replace(
        summary,
        writes=frozenset(w for w in summary.writes if live(w.parameter, w.path)),
        returns=frozenset(o for o in summary.returns if live(o.parameter, o.path)),
        transfers=frozenset(t for t in summary.transfers
                            if live(t.holder, t.path) and live(t.source, t.source_path)),
        obligations=frozenset(o for o in summary.obligations
                              if live(o.parameter, o.path) and live(o.written.parameter, o.written.path)))


def call_place(call: MIRCall, parameter: int, path: tuple[object, ...],
               slots: Mapping[MIRSlotId, MIRSlot]) -> MIRPlace:
    """The caller's place a summary's parameter path names: under the
    record a borrowed record argument points at, under the member a
    wrapper argument's path selects, or directly under a container argument
    (owned, borrowed, or a view)."""
    argument = slots[call.arguments[parameter]]
    return MIRPlace(argument.id, path_projections(argument.shape, path))


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
    member's form. A receiver member's literal at entry may take a leaf
    element's constant, as a composed record member's field does
    (`MIRMemberInit`): there are no slots before the CFG to hold it."""
    fields: tuple[MIRSlotId | MIRConstant, ...]
    # Some owned-leaf member is copied, and that copy can exit by exception
    # (`TypeDef.copy_may_raise`); a container literal allocates, so always.
    may_raise: bool = False


@dataclass(frozen=True)
class MIRTupleConstruct:
    elements: tuple[MIRSlotId | MIRConstruct, ...]


@dataclass(frozen=True)
class MIRWrap:
    """Select a sum's alternative (an Optional's 0 = absent, 1 = payload; a
    union's by its layout, None first), from the payload operand; None for
    an absent alternative."""
    alternative: int
    source: MIRPlace | None = None


@dataclass(frozen=True)
class MIRIsAlternative:
    source: MIRPlace
    alternatives: tuple[int, ...]


@dataclass(frozen=True)
class MIRUnionExtract:
    source: MIRPlace


@dataclass(frozen=True)
class MIRCopy:
    """A copy of the value at `source`. A sum or a tuple copies its
    selection and converts member by member, by what the target member
    holds: a holder takes the source member's loan, or a loan of an inline
    source member (`optional_to_ptr`); storage takes a copy; a scalar the
    same value."""
    source: MIRPlace
    # The copy can exit by exception: a buffer allocation a bare `except:`
    # catches (an owned leaf's `TypeDef.copy_may_raise`; for a record, any
    # buffer in its whole layout, `layout_copy_may_raise`; for a wrapper,
    # its storage members'), or an element access of the source.
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
             | MIRConstruct | MIRCopy | MIRMove | MIRTupleConstruct
             | MIRWrap | MIRIsAlternative | MIRUnionExtract
             | MIRIteratorInit | MIRIteratorHasNext | MIRIteratorRead | MIRIteratorAdvance | MIRRangeAdvance)


class MIRStorageWriteMode(Enum):
    # No old value: once per lifetime of the target slot's storage (the
    # body, or each entry of its region -- `MIRSlot.storage_duration`).
    INITIALIZE = auto()
    # A holder's designated backing site written (it may be revisited, each
    # loop pass) and the holder repointed to it; the former referent may
    # stay observable.
    OWN_SITE = auto()
    # The current value replaced in its storage: every alias sees it.
    IN_PLACE = auto()
    # An Optional record backing filled: a replacement only if engaged.
    OPTIONAL_ASSIGN = auto()


@dataclass(frozen=True)
class MIRStorageWrite:
    mode: MIRStorageWriteMode
    # The holder an in-place write rebuilds the object of: it repoints to
    # the new value, so retention does not count its own leaves.
    rebind_owner: MIRSlotId | None = None


@dataclass(frozen=True)
class MIRAssign:
    target: MIRPlace
    value: MIRRvalue
    loc: SourceLocation | None = None
    storage_write: MIRStorageWrite | None = None


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
    the literal its `MIRConstruct` builds over parameters and constants; a record member
    built by its own constructor is MOVEd from its `MIRMemberInits`. An
    Optional record member is left empty (`MIRWrap(0)`), COPYs
    the payload an Optional record parameter lends, or MOVEs a payload its
    own constructor builds (`MIRMemberInits`)."""
    source: MIRSlotId | MIRConstant | MIRConstruct | MIRMemberInits | MIRWrap
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
                operands.extend(f for f in fields if isinstance(f, MIRSlotId))
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
    # How each parameter slot is bound (`call_contract.parameter_binding`),
    # receiver first: the lowering's, which a summary publishes.
    parameter_bindings: tuple[MIRParameterBinding, ...] = ()
    # The declared fields of every record the body may name
    # (`MIRDefinitions.fields`): what field membership reads.
    record_fields: RecordFields = field(default=NO_FIELDS, compare=False)
    # How the result is returned, decided once by the lowering: as the
    # body's signature publishes it, a body with none as its declared type
    # does. What the validator checks the result against.
    return_representation: Representation | None = None
    # The container types whose definitions the body registered beside its
    # record layouts: what container storage's definition check reads.
    containers: tuple[TpyType, ...] = ()

    @cached_property
    def record_layouts(self) -> MIRRecords:
        """The body's record layouts by record type, with the declared fields."""
        return MIRRecords(MappingProxyType({r.type: r for r in self.records}), self.record_fields, self.containers)

    @cached_property
    def slot_bindings(self) -> 'Mapping[MIRSlotId, MIRParameterBinding]':
        """Each parameter slot's binding, paired in order; a count mismatch is
        refused here, before any reader pairs them."""
        params = [s.id for s in self.slots if s.kind is MIRSlotKind.PARAMETER]
        if len(params) != len(self.parameter_bindings):
            raise MIRValidationError("parameter slots differ from their bindings")
        return MappingProxyType(dict(zip(params, self.parameter_bindings)))


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
