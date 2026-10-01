"""The MIR verdict of every emitted body: one enumerator over the THIR caches
codegen left behind. Every emitted body of a module has exactly one
`MIRBodyVerdict`, and the verdict ladder is derived here only."""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from enum import Enum, auto

from ..codegen_cpp.context import CodeGenContext
from ..identity_map import IdentityMap
from ..parse import SourceLocation, TpyFunction, TpyModule, TpyRecord
from ..sema.analyzer import SemanticAnalyzer
from ..thir.lower import iter_module_callables, iter_module_constructors
from ..thir.nodes import THIRConstructor, THIRFunction, THIRFunctionIdentity
from ..thir.reject import is_bodyless_binding
from ..typesys import TpyType
from .call_contract import MIRSummaryResult
from .call_effects import MIRCallEffects, analyze_call_effects, dump_call_effects
from .definitions import MIRDefinitions
from .dependencies import MIRDependencies, analyze_dependencies, dump_dependencies
from .dump import dump_function
from .liveness import MIRLiveness, analyze_liveness, dump_liveness
from .lower import lower_constructor, lower_function
from .nodes import MIRBodyId, MIRBodyKind, MIRFunction, MIRNotCovered, function_body_kind
from .payload_lifetime import (
    MIRPayloadInspection, dump_payload_ends, dump_payload_inspection, inspect_payload_lifetimes,
)
from .retention import MIRRetention, analyze_retention, dump_retention
from .scope_lifetime import MIRScopeInspection, dump_scope_ends, dump_scope_inspection, inspect_scope_lifetimes
from .storage import MIRStorageEvents, analyze_storage, dump_storage
from .storage_adapter import MIRStorageRequest, certify_thir_storage
from .storage_evidence import MIRStorageVerdict
from ..mir_workspace import MIRCallWorkspace


def _declaration_name(name: str, func: TpyFunction | None) -> str:
    if func is not None and func.loc is not None:
        return f"{name}@{func.loc.line}:{func.loc.column}"
    return name


def call_definitions(ctx: CodeGenContext, module_name: str) -> tuple[tuple[MIRBodyId, THIRFunction], ...]:
    return tuple((MIRBodyId(module_name, _declaration_name(node.name, node)), fn)
                 for node, fn in ctx.thir_functions.items() if fn.resolved_callee is not None)


# --- the ladder ----------------------------------------------------------------

class MIRVerdictStatus(Enum):
    """How far one body got. Conflicts are orthogonal to the status: a
    lowered body with a conflict keeps its status and lists the conflict in
    `MIRBodyVerdict.conflicts`."""
    UNCOVERED = auto()   # did not lower
    INCOMPLETE = auto()  # lowered; some analysis returned MIRNotCovered
    COVERED = auto()     # lowered; every analysis complete
    CERTIFIED = auto()   # covered, and the storage certificate binds the request


class MIRStorageState(Enum):
    """The storage certificate's answer for a lowered body. A body with
    nothing to prove is NO_PROOF_REQUIRED, never CERTIFIED."""
    CERTIFIED = auto()
    CONFLICT = auto()
    NOT_COVERED = auto()
    NO_PROOF_REQUIRED = auto()
    NO_FACTS = auto()  # storage facts were never published for the body


class MIRRefusalKind(Enum):
    NO_BODY = auto()             # a bodyless binding or an overload stub
    THIR_REJECTED = auto()
    THIR_NOT_ATTEMPTED = auto()  # an earlier reject ended emission
    MIR_NOT_COVERED = auto()     # the enumerator or lowering refused the body


@dataclass(frozen=True)
class MIRRefusal:
    kind: MIRRefusalKind
    reason: str
    # The blocking node of a lowering refusal; None for a refusal made before lowering.
    node_kind: str | None = None
    loc: SourceLocation | None = None

    @property
    def display(self) -> str:
        """The text `--dump-mir` prints between the angle brackets."""
        if self.kind is MIRRefusalKind.MIR_NOT_COVERED:
            return f"MIR not covered: {self.reason}"
        return self.reason


@dataclass(frozen=True, eq=False)
class MIRBodySource:
    """One emitted body before lowering: its identity, its declaration and the
    THIR it lowers from, or the refusal that leaves it without one."""
    body: MIRBodyId
    name: str
    line: int | None
    func: TpyFunction | None
    # The receiver type a method or constructor lowers under; None for free functions and module init.
    owner: TpyType | None
    # The constructed record; set for constructors only.
    record: TpyRecord | None
    kind: MIRBodyKind | None
    source: THIRFunction | THIRConstructor | None
    refusal: MIRRefusal | None

    @property
    def summary_identity(self) -> THIRFunctionIdentity | None:
        if isinstance(self.source, THIRFunction) and self.source.resolved_callee is not None:
            return self.source.resolved_callee.identity
        return None


@dataclass(frozen=True, eq=False)
class MIRAnalyses:
    """The analyses `--dump-mir` prints over one lowered body."""
    liveness: MIRLiveness
    dependencies: MIRDependencies | MIRNotCovered
    events: MIRStorageEvents | MIRNotCovered
    scope: MIRScopeInspection
    effects: MIRCallEffects | MIRNotCovered
    retention: MIRRetention | MIRNotCovered
    payload: MIRPayloadInspection

    @property
    def gaps(self) -> tuple[tuple[str, MIRNotCovered], ...]:
        staged = (("dependencies", self.dependencies), ("scope ends", self.scope.ends),
                  ("scope conflicts", self.scope.conflicts), ("payload ends", self.payload.ends),
                  ("payload conflicts", self.payload.conflicts), ("storage", self.events),
                  ("call effects", self.effects), ("retention", self.retention))
        return tuple((name, result) for name, result in staged if isinstance(result, MIRNotCovered))

    @property
    def conflicts(self) -> tuple[str, ...]:
        found: list[str] = []
        if not isinstance(self.scope.conflicts, MIRNotCovered) and self.scope.conflicts:
            found.append("scope_end")
        if not isinstance(self.payload.conflicts, MIRNotCovered) and self.payload.conflicts:
            found.append("payload_end")
        if not isinstance(self.retention, MIRNotCovered) and self.retention.conflicts:
            found.append("replacement")
        if self.scope.freshness:
            found.append("stale_alias")
        return tuple(found)


def analyze_body(fn: MIRFunction) -> MIRAnalyses:
    liveness = analyze_liveness(fn)
    dependencies = analyze_dependencies(fn, liveness)
    events = analyze_storage(fn)
    return MIRAnalyses(liveness, dependencies, events, inspect_scope_lifetimes(fn),
                       analyze_call_effects(fn, dependencies),
                       analyze_retention(fn, liveness, dependencies, events), inspect_payload_lifetimes(fn))


@dataclass(frozen=True)
class MIRStorageCheck:
    """The storage certificate's answer, with its first gap or its conflict kinds."""
    state: MIRStorageState
    # The first gap: the blocking node's kind (None for a gap no node names) and its reason.
    gap_kind: str | None = None
    gap_reason: str | None = None
    conflicts: tuple[str, ...] = ()

    @property
    def gap(self) -> str | None:
        if self.gap_reason is None:
            return None
        return f"{self.gap_kind}: {self.gap_reason}" if self.gap_kind is not None else self.gap_reason


def check_storage(source: THIRFunction | THIRConstructor, body: MIRBodyId, kind: MIRBodyKind,
                  definitions: MIRDefinitions,
                  summaries: Mapping[THIRFunctionIdentity, MIRSummaryResult]) -> MIRStorageCheck:
    request = MIRStorageRequest(source, body, kind, definitions, summaries)
    bound = certify_thir_storage(request)
    requires = bound.requires_proof
    if requires is None:
        return MIRStorageCheck(MIRStorageState.NO_FACTS, gap_reason="storage facts have not been published")
    if not requires:
        return MIRStorageCheck(MIRStorageState.NO_PROOF_REQUIRED)
    verdict = bound.verdict
    if verdict is MIRStorageVerdict.CERTIFIED:
        if bound.function is not None and bound.certifies(request, source, bound.function):
            return MIRStorageCheck(MIRStorageState.CERTIFIED)
        return MIRStorageCheck(MIRStorageState.NOT_COVERED, gap_reason="certificate does not bind the request")
    if verdict is MIRStorageVerdict.CONFLICT:
        return MIRStorageCheck(MIRStorageState.CONFLICT, conflicts=tuple(sorted(
            {c.kind.name.lower() for c in bound.evidence.conflicts})))
    gaps = list(bound.gaps) + (list(bound.evidence.gaps) if bound.evidence is not None else [])
    if not gaps:
        return MIRStorageCheck(MIRStorageState.NOT_COVERED, gap_reason="no evidence")
    return MIRStorageCheck(MIRStorageState.NOT_COVERED, gap_kind=gaps[0].node_kind, gap_reason=gaps[0].reason)


@dataclass(frozen=True, eq=False)
class MIRBodyVerdict:
    body: MIRBodyId
    name: str  # the bare def name; `__init__` for constructors
    line: int | None  # the def line; None for module initialization
    kind: MIRBodyKind | None
    status: MIRVerdictStatus
    # UNCOVERED: the refusal's reason; INCOMPLETE: "<analysis>: <reason>" of the first gap.
    reason: str | None
    conflicts: tuple[str, ...]
    exceptional_exits: bool
    # None for a body that did not lower.
    storage: MIRStorageCheck | None
    refusal: MIRRefusal | None
    function: MIRFunction | None
    analyses: MIRAnalyses | None
    summary: MIRSummaryResult | None

    def describe(self) -> str:
        match self.status:
            case MIRVerdictStatus.UNCOVERED | MIRVerdictStatus.INCOMPLETE:
                text = f"{self.status.name.lower()} ({self.reason})"
            case MIRVerdictStatus.COVERED:
                assert self.storage is not None
                gap = f": {self.storage.gap}" if self.storage.gap else ""
                text = f"covered (storage: {self.storage.state.name.lower()}{gap})"
            case _:
                text = "certified"
        return text + (f", conflicts: {', '.join(self.conflicts)}" if self.conflicts else "")


# --- the enumerator ---------------------------------------------------------------

def enumerate_body_sources(module: TpyModule, analyzer: SemanticAnalyzer, ctx: CodeGenContext,
                           module_name: str, reasons: IdentityMap) -> Iterator[MIRBodySource]:
    """Every emitted body of the module in `--dump-mir` order, under the
    identities the call workspace uses."""
    identities: dict[str, int] = {}

    def identity(name: str, func: TpyFunction | None = None) -> MIRBodyId:
        name = _declaration_name(name, func)
        count = identities.get(name, 0) + 1
        identities[name] = count
        return MIRBodyId(module_name, name if count == 1 else f"{name}#{count}")

    def refused(reason: str) -> MIRRefusal:
        return MIRRefusal(MIRRefusalKind.MIR_NOT_COVERED, reason)

    def missing(func: TpyFunction | TpyModule) -> MIRRefusal:
        if isinstance(func, TpyFunction) and (is_bodyless_binding(func) or func.is_overload_stub):
            return MIRRefusal(MIRRefusalKind.NO_BODY, "no body to lower")
        if (why := reasons.get(func)) is not None:
            return MIRRefusal(MIRRefusalKind.THIR_REJECTED, f"THIR rejected: {why}")
        return MIRRefusal(MIRRefusalKind.THIR_NOT_ATTEMPTED, "THIR not attempted: an earlier reject ended emission")

    if module.top_level_stmts:
        refusal = refused("module initialization") if ctx.thir_top_level is not None else missing(module)
        yield MIRBodySource(identity("__tpy_init"), "__tpy_init", None, None, None, None, MIRBodyKind.MODULE,
                            None, refusal)

    for func, owner in iter_module_callables(module, analyzer):
        name = f"{owner.name}.{func.name}" if owner is not None else func.name
        body = identity(name, func)
        line = func.loc.line if func.loc is not None else None
        fn = ctx.thir_functions.get(func)
        refusal: MIRRefusal | None = None
        if is_bodyless_binding(func) or func.is_overload_stub:
            refusal = missing(func)
        elif ctx.thir_resumables.get(func) is not None:
            refusal = refused("resumable body")
        elif func in ctx.thir_functions or func in ctx.thir_overload_functions:
            if func.type_params or (owner is not None and owner.type_args):
                refusal = refused("generic body")
            elif analyzer.overload_groups.get(func):
                refusal = refused("overloaded callable")
            elif fn is None:
                refusal = missing(func)
        else:
            refusal = missing(func)
        yield MIRBodySource(body, func.name, line, func, owner, None,
                            function_body_kind(fn) if fn is not None else None,
                            fn if refusal is None else None, refusal)

    for record, ctor, owner in iter_module_constructors(module, analyzer):
        body = identity(f"{record.name}.__init__", ctor)
        line = ctor.loc.line if ctor.loc is not None else None
        source = ctx.thir_constructors.get(ctor)
        refusal = None
        if source is None:
            refusal = missing(ctor)
        elif record.type_params:
            refusal = refused("generic constructor")
        yield MIRBodySource(body, ctor.name, line, ctor, owner, record, MIRBodyKind.CONSTRUCTOR,
                            source if refusal is None else None, refusal)


def lower_body(source: MIRBodySource, definitions: MIRDefinitions, workspace: MIRCallWorkspace | None,
               *, fresh: bool = False) -> MIRFunction | MIRNotCovered:
    """Lower one body; a function takes the call workspace's lowering, which
    is the one its callers' summaries were computed from. `fresh` lowers
    outside the workspace cache so a caller's hooks observe the lowering;
    the result equals the cached one, since the workspace lowered each body
    over its callees' final summaries."""
    assert source.source is not None and source.refusal is None
    summaries = workspace.summaries if workspace is not None else None
    if isinstance(source.source, THIRConstructor):
        return lower_constructor(source.source, source.body, definitions=definitions, summaries=summaries)
    cached = workspace.bodies.get(source.body) if workspace is not None and not fresh else None
    if cached is not None:
        return cached
    return lower_function(source.source, source.body, definitions=definitions, summaries=summaries)


def verdict_of(source: MIRBodySource, definitions: MIRDefinitions,
               workspace: MIRCallWorkspace | None, *, fresh: bool = False) -> MIRBodyVerdict:
    """Lower one body (`fresh` as in `lower_body`), run the analyses and ask
    for the storage certificate. A MIR exception propagates: it is a
    compiler defect, not a verdict."""
    identity = source.summary_identity
    summary = workspace.summaries.get(identity) if workspace is not None and identity is not None else None

    def verdict(status: MIRVerdictStatus, reason: str | None, *, refusal: MIRRefusal | None = None,
                conflicts: tuple[str, ...] = (), exceptional_exits: bool = False,
                storage: MIRStorageCheck | None = None, function: MIRFunction | None = None,
                analyses: MIRAnalyses | None = None) -> MIRBodyVerdict:
        return MIRBodyVerdict(source.body, source.name, source.line, source.kind, status, reason, conflicts,
                              exceptional_exits, storage, refusal, function, analyses, summary)

    if source.refusal is not None:
        return verdict(MIRVerdictStatus.UNCOVERED, source.refusal.reason, refusal=source.refusal)
    lowered = lower_body(source, definitions, workspace, fresh=fresh)
    if isinstance(lowered, MIRNotCovered):
        refusal = MIRRefusal(MIRRefusalKind.MIR_NOT_COVERED, lowered.reason, lowered.node_kind, lowered.loc)
        return verdict(MIRVerdictStatus.UNCOVERED, lowered.reason, refusal=refusal)
    assert source.source is not None and source.kind is not None
    analyses = analyze_body(lowered)
    storage = check_storage(source.source, source.body, source.kind, definitions,
                            workspace.summaries if workspace is not None else {})
    conflicts = analyses.conflicts + tuple(k for k in storage.conflicts if k not in analyses.conflicts)
    if gaps := analyses.gaps:
        name, gap = gaps[0]
        status, reason = MIRVerdictStatus.INCOMPLETE, f"{name}: {gap.reason}"
    elif storage.state is MIRStorageState.CERTIFIED:
        status, reason = MIRVerdictStatus.CERTIFIED, None
    else:
        status, reason = MIRVerdictStatus.COVERED, None
    return verdict(status, reason, conflicts=conflicts, exceptional_exits=lowered.exceptional_exits,
                   storage=storage, function=lowered, analyses=analyses)


def enumerate_bodies(module: TpyModule, analyzer: SemanticAnalyzer, ctx: CodeGenContext, module_name: str,
                     definitions: MIRDefinitions, reasons: IdentityMap,
                     workspace: MIRCallWorkspace | None = None) -> tuple[MIRBodyVerdict, ...]:
    """One verdict per emitted body of the module. Run inside
    `Compiler.mir_analysis`: lowering reads the compilation's TypeDefs."""
    return tuple(verdict_of(source, definitions, workspace)
                 for source in enumerate_body_sources(module, analyzer, ctx, module_name, reasons))


# --- the dump ---------------------------------------------------------------------

def dump_codegen_mir(module: TpyModule, analyzer: SemanticAnalyzer,
                     ctx: CodeGenContext, module_name: str,
                     definitions: MIRDefinitions,
                     reasons: IdentityMap, workspace: MIRCallWorkspace | None = None) -> str:
    """Inspect emitted bodies without making MIR an emission prerequisite."""
    lines: list[str] = []
    for verdict in enumerate_bodies(module, analyzer, ctx, module_name, definitions, reasons, workspace):
        if verdict.refusal is not None:
            lines.append(f"fn {verdict.body.module}::{verdict.body.declaration}: <{verdict.refusal.display}>\n")
            continue
        fn, analyses = verdict.function, verdict.analyses
        assert fn is not None and analyses is not None
        lines.append(dump_function(fn).rstrip("\n") + "\n")
        lines.append(dump_liveness(analyses.liveness))
        lines.append(dump_scope_ends(analyses.scope.ends))
        lines.append(dump_scope_inspection(analyses.scope))
        lines.append(dump_dependencies(analyses.dependencies))
        lines.append(dump_call_effects(analyses.effects))
        lines.append(dump_storage(analyses.events))
        lines.append(dump_retention(analyses.retention))
        lines.append(dump_payload_ends(analyses.payload.ends))
        lines.append(dump_payload_inspection(analyses.payload))
    return "\n".join(lines) if lines else "(no emitted bodies in this module)\n"
