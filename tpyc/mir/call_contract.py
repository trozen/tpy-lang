"""Immutable certificates for the bounded normal-returning call interface."""

from dataclasses import dataclass
from enum import Enum, auto

from ..thir.nodes import (
    THIRBorrowedRecord, THIRCallableSignature, THIRFieldIdentity, THIRFunctionIdentity, THIRResolvedCallee,
)
from ..typesys import BOOL, INT32, NominalType, VoidType, unwrap_readonly, unwrap_ref_type


@dataclass(frozen=True)
class MIRParameterWrite:
    parameter: int
    path: tuple[THIRFieldIdentity, ...]


@dataclass(frozen=True)
class MIRCallSummary:
    """Parameter effects; globals, environments and other exits are excluded.

    Only parameter-rooted effects are representable. Other roots require an
    explicit contract, not interpreting an absent root as an empty effect.
    """
    callee: THIRResolvedCallee
    parameters: tuple[THIRBorrowedRecord | None, ...]
    reads: frozenset[int]
    writes: frozenset[MIRParameterWrite]
    invalidates: frozenset[int]
    returns: frozenset[int]
    retains: frozenset[int]
    normal_return_only: bool


def summary_problem(summary: MIRCallSummary) -> str | None:
    """Validate the bounded contract without re-proving its supplying body."""
    if not isinstance(summary, MIRCallSummary) or not isinstance(summary.callee, THIRResolvedCallee):
        return "invalid call summary"
    identity = summary.callee.identity
    signature = summary.callee.signature
    if (not isinstance(identity, THIRFunctionIdentity) or not identity.module or not identity.name
            or not isinstance(signature, THIRCallableSignature)
            or not isinstance(signature.param_types, tuple) or not isinstance(summary.parameters, tuple)
            or any(not isinstance(indices, frozenset) or any(type(i) is not int for i in indices)
                   for indices in (summary.reads, summary.invalidates,
                                   summary.returns, summary.retains))):
        return "invalid call summary identity or facts"
    if ((signature.return_type not in (BOOL, INT32) and not isinstance(signature.return_type, VoidType))
            or len(summary.parameters) != len(signature.param_types)
            or summary.reads != frozenset(range(len(summary.parameters)))
            or not isinstance(summary.writes, frozenset)
            or any((summary.invalidates, summary.returns, summary.retains))
            or summary.normal_return_only is not True):
        return "unsupported call summary contract"
    for typ, ref in zip(signature.param_types, summary.parameters):
        bare = unwrap_readonly(unwrap_ref_type(typ))
        if ref is None:
            if typ not in (BOOL, INT32):
                return "unsupported scalar call parameter"
        elif (not isinstance(ref, THIRBorrowedRecord) or ref.type != bare
              or not isinstance(bare, NominalType) or bare in (BOOL, INT32)
              or type(ref.readonly) is not bool):
            return "unsupported record call parameter"
    for write in summary.writes:
        if (not isinstance(write, MIRParameterWrite) or type(write.parameter) is not int
                or not 0 <= write.parameter < len(summary.parameters)
                or not isinstance(write.path, tuple) or len(write.path) != 1):
            return "invalid call write path"
        ref = summary.parameters[write.parameter]
        field = write.path[0]
        if (ref is None or ref.readonly or not isinstance(field, THIRFieldIdentity)
                or field.owner != ref.type or not field.name or field.type not in (BOOL, INT32)):
            return "unsupported call write field or access"
    return None


def reader_call_problem(summary: MIRCallSummary) -> str | None:
    """The scalar reader consumer deliberately lags the richer summary contract."""
    return ("call needs scalar reader summary" if summary.writes
            or summary.callee.signature.return_type not in (BOOL, INT32) else None)


class MIRSummaryState(Enum):
    PENDING = auto()
    OPAQUE = auto()
    KNOWN = auto()


@dataclass(frozen=True)
class MIRSummaryResult:
    state: MIRSummaryState
    summary: MIRCallSummary | None = None
    reason: str | None = None

    def __post_init__(self) -> None:
        match self.state:
            case MIRSummaryState.PENDING:
                valid = self.summary is None and self.reason is None
            case MIRSummaryState.OPAQUE:
                valid = self.summary is None and bool(self.reason)
            case MIRSummaryState.KNOWN:
                valid = isinstance(self.summary, MIRCallSummary) and self.reason is None
            case _:
                valid = False
        if not valid:
            raise ValueError("inconsistent MIR summary state")

    @classmethod
    def opaque(cls, reason: str) -> 'MIRSummaryResult':
        return cls(MIRSummaryState.OPAQUE, reason=reason)
