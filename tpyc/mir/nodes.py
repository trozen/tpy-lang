"""Immutable MIR with body-scoped holders and logical storage projections."""

from dataclasses import dataclass
from enum import Enum, auto

from ..parse import SourceLocation
from ..thir.nodes import Form, THIRBorrowedRecord
from ..type_def_registry import ParamPassing
from ..typesys import NominalType, TpyType
from .call_contract import MIRCallSummary


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
    BORROWED_RECORD = auto()
    RECORD_STORAGE = auto()
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
    """The source type and element form of a native iterator, never its C++ type."""
    element: MIRTupleElement


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
class MIRGlobalId:
    module: str
    name: str


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
class MIRContainerStructure:
    pass


@dataclass(frozen=True)
class MIRContainerElements:
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


@dataclass(frozen=True)
class MIRPlace:
    root: MIRSlotId
    projections: tuple[MIRDeref | MIRField | MIRTupleIndex | MIROptionalPayload | MIRUnionPayload
                       | MIRContainerStructure | MIRContainerElements, ...] = ()


@dataclass(frozen=True)
class MIRConstant:
    # The Python value of a scalar leaf constant (`scalar_leaves.leaf_constant`).
    value: int | bool | float | str


@dataclass(frozen=True)
class MIRCall:
    summary: MIRCallSummary
    arguments: tuple[MIRSlotId, ...]


@dataclass(frozen=True)
class MIRRead:
    source: MIRPlace


@dataclass(frozen=True)
class MIRAlias:
    source: MIRSlotId


@dataclass(frozen=True)
class MIRBorrow:
    source: MIRPlace


@dataclass(frozen=True)
class MIRConstruct:
    fields: tuple[MIRSlotId, ...]


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
    """A certified primitive operation (`THIRBinOp.certified_op` /
    `THIRUnaryArith.certified_op`): it reads its inert-leaf operands by
    value and allocates, retains and borrows nothing. `op` names it for
    inspection only. `may_raise`: it can exit by exception (checked
    overflow, a zero divisor), which the call-summary interface does not
    model."""
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


@dataclass(frozen=True)
class MIRReceiverInit:
    """Initialize supplied storage before the CFG can observe the receiver."""
    receiver: MIRSlotId
    fields: tuple[MIRSlotId | MIRConstant, ...]


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


@dataclass(frozen=True)
class MIRNotCovered:
    body: MIRBodyId
    node_kind: str
    reason: str
    loc: SourceLocation | None = None
