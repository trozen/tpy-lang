"""Immutable certificates for the bounded normal-returning call interface."""

from dataclasses import dataclass
from enum import Enum, auto

from ..thir.nodes import THIRBorrowedRecord, THIRCallableSignature, THIRFunctionIdentity, THIRResolvedCallee
from ..typesys import BOOL, INT32, NominalType, unwrap_readonly, unwrap_ref_type


@dataclass(frozen=True)
class MIRCallSummary:
    """Complete reader-only proof; globals, environments and other exits are excluded.

    Only parameter-rooted reads are representable. Other roots require an
    explicit contract, not interpreting an absent root as an empty effect.
    """
    callee: THIRResolvedCallee
    parameters: tuple[THIRBorrowedRecord | None, ...]
    reads: frozenset[int]
    writes: frozenset[int]
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
                   for indices in (summary.reads, summary.writes, summary.invalidates,
                                   summary.returns, summary.retains))):
        return "invalid call summary identity or facts"
    if (signature.return_type not in (BOOL, INT32)
            or len(summary.parameters) != len(signature.param_types)
            or summary.reads != frozenset(range(len(summary.parameters)))
            or any((summary.writes, summary.invalidates, summary.returns, summary.retains))
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
    return None


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
