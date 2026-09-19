"""Immutable MIR with body-scoped holders and logical storage projections."""

from dataclasses import dataclass
from enum import Enum, auto

from ..parse import SourceLocation
from ..thir.nodes import Form
from ..typesys import NominalType, TpyType


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


class MIRStorageDuration(Enum):
    BODY = auto()
    CALLER = auto()


@dataclass(frozen=True)
class MIRTupleElement:
    type: TpyType
    kind: MIRValueKind = MIRValueKind.SCALAR
    readonly: bool = False


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
    storage_duration: MIRStorageDuration | None = None


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


@dataclass(frozen=True)
class MIRPlace:
    root: MIRSlotId
    projections: tuple[MIRDeref | MIRField | MIRTupleIndex | MIROptionalPayload | MIRUnionPayload, ...] = ()


@dataclass(frozen=True)
class MIRConstant:
    value: int | bool


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
    elements: tuple[MIRSlotId, ...]


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


MIRRvalue = (MIRConstant | MIRRead | MIRCompare | MIRNot | MIRAlias | MIRBorrow
             | MIRConstruct | MIRCopy | MIRMove | MIRTupleConstruct | MIRTupleCopy
             | MIROptionalConstruct | MIROptionalCopy | MIRIsPresent
             | MIRUnionConstruct | MIRUnionCopy | MIRIsAlternative | MIRUnionExtract)


class MIRRecordWriteMode(Enum):
    INITIALIZE_ONCE = auto()
    OWN_SITE = auto()
    IN_PLACE = auto()


@dataclass(frozen=True)
class MIRRecordWrite:
    mode: MIRRecordWriteMode
    rebind_owner: MIRSlotId | None = None


class MIRPayloadWriteMode(Enum):
    INITIALIZE = auto()
    ASSIGN = auto()


@dataclass(frozen=True)
class MIRPayloadWrite:
    mode: MIRPayloadWriteMode


@dataclass(frozen=True)
class MIRAssign:
    target: MIRPlace
    value: MIRRvalue
    loc: SourceLocation | None = None
    storage_write: MIRRecordWrite | MIRPayloadWrite | None = None


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
    statements: tuple[MIRAssign, ...]
    terminator: MIRTerminator


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


@dataclass(frozen=True)
class MIRNotCovered:
    body: MIRBodyId
    node_kind: str
    reason: str
    loc: SourceLocation | None = None
