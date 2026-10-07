"""Immutable MIR with body-scoped holders and logical storage projections."""

from dataclasses import dataclass
from enum import Enum, auto

from ..parse import SourceLocation
from ..thir.nodes import Form, THIRBorrowedRecord, THIRFunction
from ..type_def_registry import ParamPassing
from ..typesys import NominalType, TpyType
from .call_contract import MIRCallSummary, MIRContainerElements, MIRContainerStructure, MIRGlobalId


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


class MIRValueKind(Enum):
    SCALAR = auto()
    BORROWED = auto()
    OWNED = auto()
    TUPLE = auto()
    OPTIONAL = auto()
    UNION = auto()
    PAYLOAD_ALIAS = auto()
    BORROWED_CONTAINER = auto()
    NATIVE_ITERATOR = auto()


class MIRStorageDuration(Enum):
    BODY = auto()
    CALLER = auto()


class MIRRecordStorageKind(Enum):
    DIRECT = auto()
    OPTIONAL = auto()


@dataclass(frozen=True)
class MIRTupleElement:
    type: TpyType
    kind: MIRValueKind = MIRValueKind.SCALAR
    readonly: bool = False


@dataclass(frozen=True)
class MIRContainerLayout:
    """The member forms of a native container's storage, never its C++ type.
    `element` is what iteration yields (a list / set / Array element, a dict
    key), `value` a dict's value or None. A member is SCALAR (an inert
    leaf), OWNED (an owned leaf) or BORROWED (a plain record); its access
    is the container's. Element identity is never tracked: `[elements]` is
    one region every subscript, iterator and view of the container reaches."""
    element: MIRTupleElement
    value: MIRTupleElement | None = None

    @property
    def subscript(self) -> MIRTupleElement:
        """The member a subscript yields: a dict's value, else the element."""
        return self.value if self.value is not None else self.element


@dataclass(frozen=True)
class MIRTupleLayout:
    elements: tuple[MIRTupleElement, ...]


@dataclass(frozen=True)
class MIROptionalLayout:
    type: TpyType
    kind: MIRValueKind = MIRValueKind.SCALAR
    readonly: bool = False


@dataclass(frozen=True)
class MIRUnionLayout:
    elements: tuple[MIRTupleElement | None, ...]


@dataclass(frozen=True)
class MIRSlot:
    id: MIRSlotId
    type: TpyType
    kind: MIRSlotKind
    name: str | None = None
    form: Form = Form.VALUE
    value_kind: MIRValueKind = MIRValueKind.SCALAR
    readonly: bool = False
    tuple_layout: MIRTupleLayout | None = None
    optional_layout: MIROptionalLayout | None = None
    union_layout: MIRUnionLayout | None = None
    alias_source: 'MIRPlace | None' = None
    global_id: MIRGlobalId | None = None
    storage_duration: MIRStorageDuration | MIRRegionId | None = None
    residence: MIRRegionId | None = None
    record_storage: MIRRecordStorageKind = MIRRecordStorageKind.DIRECT
    container_layout: MIRContainerLayout | None = None
    # How the signature passes a PARAMETER slot (`THIRParam.passing`);
    # None elsewhere, and a parameter without it is no scalar leaf.
    passing: ParamPassing | None = None


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
    """Build a record from one operand per field: an inert leaf by value, or
    an owned leaf copied out of the storage a borrowed holder lends (the
    constructor's member initializer copies it) or moved out of owned
    temporary storage the call hands over. Into a container layout it is a
    literal: one operand per element (a dict's keys and values alternate),
    each by its member's form."""
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
    source: MIRSlotId | None = None


@dataclass(frozen=True)
class MIROptionalCopy:
    source: MIRSlotId


@dataclass(frozen=True)
class MIRIsPresent:
    source: MIRSlotId


@dataclass(frozen=True)
class MIRUnionConstruct:
    alternative: int
    source: MIRSlotId | None = None


@dataclass(frozen=True)
class MIRUnionCopy:
    source: MIRSlotId


@dataclass(frozen=True)
class MIRIsAlternative:
    source: MIRSlotId
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
    # An inert leaf, by value.
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
    built by its own constructor is MOVEd from its `MIRMemberInits`."""
    source: MIRSlotId | MIRConstant | MIRConstruct | MIRMemberInits
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


@dataclass(frozen=True)
class MIRNotCovered:
    body: MIRBodyId
    node_kind: str
    reason: str
    loc: SourceLocation | None = None
