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


class MIRSlotKind(Enum):
    PARAMETER = auto()
    LOCAL = auto()
    TEMPORARY = auto()


class MIRValueKind(Enum):
    SCALAR = auto()
    BORROWED_RECORD = auto()


@dataclass(frozen=True)
class MIRSlot:
    id: MIRSlotId
    type: TpyType
    kind: MIRSlotKind
    name: str | None = None
    form: Form = Form.VALUE
    value_kind: MIRValueKind = MIRValueKind.SCALAR
    readonly: bool = False


@dataclass(frozen=True)
class MIRDeref:
    pass


@dataclass(frozen=True)
class MIRFieldId:
    owner: NominalType
    name: str


@dataclass(frozen=True)
class MIRField:
    id: MIRFieldId
    type: TpyType


@dataclass(frozen=True)
class MIRPlace:
    root: MIRSlotId
    projections: tuple[MIRDeref | MIRField, ...] = ()


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
class MIRCompare:
    op: str
    left: MIRSlotId
    right: MIRSlotId


@dataclass(frozen=True)
class MIRNot:
    operand: MIRSlotId


MIRRvalue = MIRConstant | MIRRead | MIRCompare | MIRNot | MIRAlias


@dataclass(frozen=True)
class MIRAssign:
    target: MIRPlace
    value: MIRRvalue
    loc: SourceLocation | None = None


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
class MIRFunction:
    id: MIRBodyId
    return_type: TpyType
    slots: tuple[MIRSlot, ...]
    blocks: tuple[MIRBlock, ...]
    entry: MIRBlockId


@dataclass(frozen=True)
class MIRNotCovered:
    body: MIRBodyId
    node_kind: str
    reason: str
    loc: SourceLocation | None = None
