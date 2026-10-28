"""The MIR verdict of every emitted body: one enumerator over the THIR caches
codegen left behind. Every emitted body of a module has exactly one
`MIRBodyVerdict`, and the verdict ladder is derived here only."""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from functools import cached_property
from enum import Enum, auto
from types import MappingProxyType

from ..codegen_cpp.context import CodeGenContext
from ..identity_map import IdentityMap
from ..parse import SourceLocation, TpyFunction, TpyModule, TpyRecord
from ..sema.analyzer import SemanticAnalyzer
from ..thir.lower import iter_module_callables, iter_module_constructors
from ..thir.nodes import THIRConstructor, THIRFunction, THIRFunctionIdentity
from ..thir.reject import is_bodyless_binding
from ..typesys import TpyType
from .call_contract import MIRAliasObligation, MIRSummaryResult, MIRSummaryState
from .call_effects import MIRCallEffects, analyze_call_effects, dump_call_effects
from .definitions import MIRDefinitions, view_members
from .dependencies import (
    MIRDependencies, MIRReferent, MIRUnseededLoan, _leaves, dump_dependencies,
    resolve_referents,
)
from .dump import dump_function
from .liveness import MIRLiveness, MIRPoint, dump_liveness
from .emit import slot_occurrences
from .lower import MIRLoweredStorage, lower_callable
from .nodes import (
    MIRAssign, MIRBlockId, MIRBodyId, MIRBodyKind, MIRCopy, MIRFunction, MIRNotCovered,
    MIRPlace, MIRSlot, MIRSlotId, MIRStorageWriteMode, function_body_kind, source_order, statement_target,
    place_shape,
)
from .value import MIRRecordLayout, MIRShape
from .payload_lifetime import (
    MIRPayloadInspection, dump_payload_ends, dump_payload_inspection, inspect_payload_lifetimes,
)
from .prepare import MIRPrepared
from .retention import (
    MIRRetention, MIRRetentionClasses, analyze_retention, classify_retention, dump_retention, static_referent,
)
from .scope_lifetime import MIRScopeInspection, dump_scope_ends, dump_scope_inspection, inspect_scope_lifetimes
from .shapes import (
    MIRUnionPayload, object_prefix,
    optional_payload,
)
from .reach import storage_destination
from .place_text import AnnotationSpelling, MIRStorageNames
from .storage import MIRStorageEvents, analyze_storage, dump_storage
from .storage_adapter import MIRStorageRequest, certify_thir_storage
from .storage_evidence import (
    MIRStorageConflict, MIRStorageVerdict, analyze_escapes, dump_escapes,
)
from .witness import MIRHazard, MIRHazardKind, MIRWitness, decide, dump_witness_cap
from ..mir_workspace import MIRCallWorkspace, callable_identity


def _declaration_name(name: str, func: TpyFunction | None) -> str:
    if func is not None and func.loc is not None:
        return f"{name}@{func.loc.line}:{func.loc.column}"
    return name


def body_declaration(func: TpyFunction, owner: TpyType | None) -> str:
    """The declaration a function body is keyed by -- `Owner.name@line:col`
    for a method -- in the call workspace and in the verdict walk alike, so
    the walk reads the lowering the workspace cached."""
    return _declaration_name(f"{owner.name}.{func.name}" if owner is not None else func.name, func)


def call_definitions(ctx: CodeGenContext, module_name: str
                     ) -> tuple[tuple[MIRBodyId, THIRFunction | THIRConstructor], ...]:
    """Every body publishing a callee identity -- its definition and an
    `@auto_readonly` def's access twin, and each constructor -- under the
    id the verdict walk gives it: the clones of a def share one
    declaration, so the second lowered body is `#2` as in
    `enumerate_body_sources`. A cache entry is read back only for the very
    body it lowered (`MIRCallWorkspace.lowering`). A generic record's
    constructor is no callee MIR summarizes."""
    seen: dict[str, int] = {}
    result: list[tuple[MIRBodyId, THIRFunction | THIRConstructor]] = []

    def body(name: str) -> MIRBodyId:
        count = seen[name] = seen.get(name, 0) + 1
        return MIRBodyId(module_name, name if count == 1 else f"{name}#{count}")
    for node, fn in ctx.thir_functions.items():
        name = body(body_declaration(node, fn.receiver.type if fn.receiver else None))
        if fn.resolved_callee is not None:
            result.append((name, fn))
    for node, ctor in ctx.thir_constructors.items():
        layout = ctor.record_layout
        if layout is not None and not getattr(layout.type, "type_args", ()):
            result.append((body(_declaration_name(f"{ctor.record_name}.__init__", node)), ctor))
    return tuple(result)


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
        if isinstance(self.source, (THIRFunction, THIRConstructor)):
            return callable_identity(self.source)
        return None


@dataclass(frozen=True, eq=False)
class MIRAnalyses:
    """The analyses the verdict runs over one lowered body, each of which
    `--dump-mir` prints."""
    # The first dependency run's refusal for a body it refuses, as every
    # analysis below then is.
    liveness: MIRLiveness | MIRNotCovered
    dependencies: MIRDependencies | MIRNotCovered
    events: MIRStorageEvents | MIRNotCovered
    scope: MIRScopeInspection
    effects: MIRCallEffects | MIRNotCovered
    retention: MIRRetention | MIRNotCovered
    payload: MIRPayloadInspection
    escapes: tuple[MIRStorageConflict, ...] | MIRNotCovered
    store_escapes: tuple[MIRStorageConflict, ...] | MIRNotCovered
    # The no-alias obligations the body's KNOWN summary publishes: a caller
    # MIR analyzes checks each, and the body keeps them as `alias_obligation`
    # conflicts, apart from its replacements.
    obligations: frozenset[MIRAliasObligation] = frozenset()
    # The preparation the analyses ran over, and every hazard they emitted
    # beside their conflicts (`witness.MIRHazard`): what the witness reads.
    prepared: MIRPrepared | None = None
    hazards: tuple[MIRHazard, ...] = ()

    @property
    def retention_classes(self) -> MIRRetentionClasses | None:
        if isinstance(self.retention, MIRNotCovered):
            return None
        return classify_retention(self.retention, self.obligations)

    @cached_property
    def witnesses(self) -> tuple[MIRWitness, ...]:
        """Each hazard the body answers for, decided (`witness.decide`): a
        replacement only where the body answers for it (a published no-alias
        obligation is its callers' to check)."""
        if not isinstance(self.prepared, MIRPrepared):
            return ()
        classes = self.retention_classes
        answered = set(classes.replacements) if classes is not None else set()
        return decide(self.prepared, [h for h in self.hazards
                                      if h.kind is not MIRHazardKind.REPLACEMENT or answered & set(h.conflicts)])

    @property
    def gaps(self) -> tuple[tuple[str, MIRNotCovered], ...]:
        staged = (("dependencies", self.dependencies), ("scope ends", self.scope.ends),
                  ("scope conflicts", self.scope.conflicts), ("payload ends", self.payload.ends),
                  ("payload conflicts", self.payload.conflicts), ("storage", self.events),
                  ("call effects", self.effects), ("retention", self.retention),
                  ("return escapes", self.escapes), ("store escapes", self.store_escapes))
        return tuple((name, result) for name, result in staged if isinstance(result, MIRNotCovered))

    @property
    def conflicts(self) -> tuple[str, ...]:
        found: list[str] = []
        if not isinstance(self.scope.conflicts, MIRNotCovered) and self.scope.conflicts:
            found.append("scope_end")
        if not isinstance(self.payload.conflicts, MIRNotCovered) and self.payload.conflicts:
            found.append("payload_end")
        if (classes := self.retention_classes) is not None:
            if classes.replacements:
                found.append("replacement")
            if classes.argument_aliases:
                found.append("argument_alias")
            if classes.alias_obligations:
                found.append("alias_obligation")
        if self.scope.freshness:
            found.append("stale_alias")
        if not isinstance(self.escapes, MIRNotCovered) and self.escapes:
            found.append("return_escape")
        if not isinstance(self.store_escapes, MIRNotCovered) and self.store_escapes:
            found.append("store_escape")
        return tuple(found)


def analyze_body(fn: MIRFunction, prepared: MIRPrepared | MIRNotCovered,
                 obligations: frozenset[MIRAliasObligation] = frozenset()) -> MIRAnalyses:
    """The analyses over the body's preparation; a first dependency run's
    refusal is every analysis's gap."""
    if isinstance(prepared, MIRNotCovered):
        return MIRAnalyses(prepared, prepared, prepared, MIRScopeInspection(fn, prepared, prepared, ()), prepared,
                           prepared, MIRPayloadInspection(fn, prepared, prepared, ()), prepared, prepared,
                           obligations)
    events, scope, effects = analyze_storage(prepared), inspect_scope_lifetimes(prepared), analyze_call_effects(prepared)
    retention, payload = analyze_retention(prepared), inspect_payload_lifetimes(prepared)
    escapes = analyze_escapes(prepared)
    hazards = (*(retention.hazards if isinstance(retention, MIRRetention) else ()), *scope.hazards,
               *payload.hazards, *escapes.hazards)
    return MIRAnalyses(prepared.liveness, prepared.dependencies, events, scope, effects, retention, payload,
                       escapes.returns, escapes.stores, obligations, prepared, hazards)


def published_obligations(summary: MIRSummaryResult | None) -> frozenset[MIRAliasObligation]:
    """What a body's own summary publishes for its callers to discharge;
    nothing unless it is KNOWN, so an unpublished conflict stays a replacement."""
    if summary is None or summary.state is not MIRSummaryState.KNOWN:
        return frozenset()
    return summary.summary.obligations


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


def _slot_orders(fn: MIRFunction) -> dict[MIRSlotId, tuple[object, ...]]:
    """Each slot by where it first occurs -- the statement's source location,
    then its position among the slots the statement names -- and its name:
    an order no slot number decides."""
    never = (source_order(None), 0)
    first: dict[MIRSlotId, tuple[tuple[int, int, int], int]] = {}
    for block in fn.blocks:
        for stmt in (*block.statements, block.terminator):
            named: list[MIRSlotId] = []
            slot_occurrences(stmt, named)
            for position, sid in enumerate(named):
                first[sid] = min(first.get(sid, never), (source_order(stmt.loc), position))
    return {s.id: (*first.get(s.id, never), s.name or "") for s in fn.slots}


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
    evidence = bound.evidence.gaps if bound.evidence is not None else ()
    if not bound.gaps and not evidence:
        return MIRStorageCheck(MIRStorageState.NOT_COVERED, gap_reason="no evidence")
    # The binding's gaps come in THIR fact order; the evidence walks the
    # body's slots and blocks, so its first gap is picked by source location,
    # a tie by the slot it names (where it first occurs, its name), then by
    # its reason.
    slots = _slot_orders(bound.function) if bound.function is not None and len(evidence) > 1 else {}
    first = bound.gaps[0] if bound.gaps else min(
        evidence, key=lambda g: (source_order(g.loc), g.node_kind or "",
                                 slots.get(g.slot, (source_order(None), 0, "")), g.reason))
    return MIRStorageCheck(MIRStorageState.NOT_COVERED, gap_kind=first.node_kind, gap_reason=first.reason)


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

    def identity(name: str) -> MIRBodyId:
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
        carrier = analyzer.ctx.module_init_function
        refusal = refused("module initialization") if ctx.thir_top_level is not None else missing(module)
        yield MIRBodySource(identity(carrier.name), carrier.name, None, carrier, None, None, MIRBodyKind.MODULE,
                            None, refusal)

    for func, owner in iter_module_callables(module, analyzer):
        body = identity(body_declaration(func, owner))
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
        body = identity(_declaration_name(f"{record.name}.__init__", ctor))
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
               *, fresh: bool = False) -> MIRLoweredStorage | MIRNotCovered:
    """Lower one body; a function takes the call workspace's lowering, which
    is the one its callers' summaries were computed from. `fresh` lowers
    outside the workspace cache so a caller's hooks observe the lowering;
    the result equals the cached one, since the workspace lowered each body
    over its callees' final summaries."""
    assert source.source is not None and source.refusal is None
    summaries = workspace.summaries if workspace is not None else None
    cached = workspace.lowering(source.body, source.source) if workspace is not None and not fresh else None
    if cached is not None:
        return cached
    return lower_callable(source.source, source.body, definitions, summaries)


def body_summary(source: MIRBodySource, workspace: MIRCallWorkspace | None) -> MIRSummaryResult | None:
    """The call summary a body publishes, when it publishes one."""
    identity = source.summary_identity
    return workspace.summaries.get(identity) if workspace is not None and identity is not None else None


def lower_and_analyze(source: MIRBodySource, definitions: MIRDefinitions, workspace: MIRCallWorkspace | None,
                      *, fresh: bool = False) -> tuple[MIRFunction | MIRNotCovered, MIRAnalyses | None]:
    """Lower one body (`fresh` as in `lower_body`) and run the analyses
    under the obligations its own summary publishes: the verdict's middle
    step, and all a warning needs."""
    lowered = lower_body(source, definitions, workspace, fresh=fresh)
    if isinstance(lowered, MIRNotCovered):
        return lowered, None
    return lowered.function, analyze_body(lowered.function, lowered.prepared,
                                          published_obligations(body_summary(source, workspace)))


def verdict_of(source: MIRBodySource, definitions: MIRDefinitions,
               workspace: MIRCallWorkspace | None, *, fresh: bool = False) -> MIRBodyVerdict:
    """Lower one body (`fresh` as in `lower_body`), run the analyses and ask
    for the storage certificate. A MIR exception propagates: it is a
    compiler defect, not a verdict."""
    summary = body_summary(source, workspace)

    def verdict(status: MIRVerdictStatus, reason: str | None, *, refusal: MIRRefusal | None = None,
                conflicts: tuple[str, ...] = (), exceptional_exits: bool = False,
                storage: MIRStorageCheck | None = None, function: MIRFunction | None = None,
                analyses: MIRAnalyses | None = None) -> MIRBodyVerdict:
        return MIRBodyVerdict(source.body, source.name, source.line, source.kind, status, reason, conflicts,
                              exceptional_exits, storage, refusal, function, analyses, summary)

    if source.refusal is not None:
        return verdict(MIRVerdictStatus.UNCOVERED, source.refusal.reason, refusal=source.refusal)
    lowered, analyses = lower_and_analyze(source, definitions, workspace, fresh=fresh)
    if isinstance(lowered, MIRNotCovered):
        refusal = MIRRefusal(MIRRefusalKind.MIR_NOT_COVERED, lowered.reason, lowered.node_kind, lowered.loc)
        return verdict(MIRVerdictStatus.UNCOVERED, lowered.reason, refusal=refusal)
    assert source.source is not None and source.kind is not None and analyses is not None
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


# --- line facts -------------------------------------------------------------------

class MIRWriteKind(Enum):
    """What a write lands in, as the `mir_owned` / `mir_borrowed`
    annotations read it: owned storage, a borrowed holder, an inert leaf."""
    OWNED = auto()
    BORROWED = auto()
    SCALAR = auto()


@dataclass(frozen=True)
class MIRLineWrite:
    """One statement on a source line writing a named place."""
    block: int
    # What the written place is: a slot's or a wrapper member's value, OWNED
    # for record or owned-leaf storage (a field, or what a holder points at),
    # SCALAR for an inert-leaf field; None for a place that is none of them.
    kind: MIRWriteKind | None
    copy: bool  # the written value is a `MIRCopy`


@dataclass(frozen=True)
class MIRLineReferents:
    """A holder's possible referents after the last statement of a line in one block."""
    block: int
    referents: frozenset[str]


@dataclass(frozen=True)
class MIRLineFacts:
    """The per-line MIR facts of one lowered body, keyed by (line, spelling).

    A spelling is a source name plus field and member projections
    (`self.name`, `t[0]`, `xs[elements]`); dereferences are not spelled. A
    field whose name the record's layout carries under two owners (a
    shadowed inherited field) is spelled with its declaring owner
    (`self.Base.n`), and the bare spelling is listed in `ambiguous`. A
    name whose slots sit under two selectors (a reassigned `str` parameter
    owns a LOCAL slot beside its PARAMETER one) is spelled `param(s)` /
    `local(s)`. A referent is spelled by its root's name -- a borrowed
    parameter's external referent by the parameter, a global's by the global
    -- or `static` for a literal; storage with no name of its own (a
    constructed temporary) takes the name of the first named slot that
    borrows it whole, the binding that introduced it, suffixed `@<line>` of
    that binding when the name binds several such storages."""
    body: MIRBodyId
    # Each source name's selectors ("param", "local", "global") to its canonical spelling.
    names: Mapping[str, Mapping[str, str]]
    # Canonical spellings that hold loans: a holder slot and each of its leaves.
    holders: frozenset[str]
    lines: frozenset[int]
    # Every write of a named place by a statement on the line, over all blocks.
    writes: Mapping[tuple[int, str], tuple[MIRLineWrite, ...]]
    # The replacement events on the line, by destination storage: the
    # non-initializing record-write modes, `call` for a call's write, `move`
    # for a move out of owned storage.
    events: Mapping[tuple[int, str], tuple[str, ...]]
    # A holder's referents per block holding a statement on the line.
    borrows: Mapping[tuple[int, str], tuple[MIRLineReferents, ...]]
    # The analysis gap that leaves `borrows` / `events` unknown, else None.
    borrows_gap: str | None = None
    events_gap: str | None = None
    # A bare field spelling that names fields of two owners, to the
    # owner-qualified spellings it could mean.
    ambiguous: Mapping[str, tuple[str, ...]] = MappingProxyType({})


def _value_write(shape: MIRShape) -> MIRWriteKind | None:
    """What a write of a value of `shape` lands in: owned storage, a holder
    of storage elsewhere (a pointer, a container view), an inert leaf."""
    if shape.owns_storage:
        return MIRWriteKind.OWNED
    if shape.borrow_holder:
        return MIRWriteKind.BORROWED
    return MIRWriteKind.SCALAR if shape.is_scalar else None


def _written_kind(place: MIRPlace, slots: Mapping[MIRSlotId, MIRSlot],
                  records: Mapping[TpyType, MIRRecordLayout]) -> MIRWriteKind | None:
    """What a write of `place` lands in: the value it reaches, classified
    (`_value_write`: a scalar element is a scalar, a record element owned
    storage); a target whose value has no class (an Optional record field)
    is owned storage when the write replaces storage (`storage_destination`)."""
    last = place.projections[-1] if place.projections else None
    if isinstance(last, MIRUnionPayload) and not optional_payload(
            place_shape(MIRPlace(place.root, place.projections[:-1]), slots, records).cls, last):
        return None
    kind = _value_write(place_shape(place, slots, records))
    if kind is not None:
        return kind
    return MIRWriteKind.OWNED if storage_destination(place, slots) else None


def _line_write(block: MIRBlockId, place: MIRPlace, value: object, slots: Mapping[MIRSlotId, MIRSlot],
                records: Mapping[TpyType, MIRRecordLayout]) -> MIRLineWrite:
    """A write of `value` into `place` as its line's facts read it: what
    the place holds (`_written_kind`), and whether the value is a copy."""
    return MIRLineWrite(block.index, _written_kind(place, slots, records), isinstance(value, MIRCopy))


def line_facts(verdict: MIRBodyVerdict) -> MIRLineFacts | None:
    """The per-line facts of a lowered body, None for a body that did not
    lower. Reads the analyses the verdict already ran, never re-lowers; runs
    under the compilation (field kinds read its TypeDefs)."""
    fn, analyses = verdict.function, verdict.analyses
    if fn is None or analyses is None:
        return None
    slots = {slot.id: slot for slot in fn.slots}
    records = fn.record_layouts
    _spell = AnnotationSpelling(fn)
    naming = MIRStorageNames(fn)
    names, named = naming.names, naming.named
    # Every storage's one name, a holder's own or a binding's: a write event
    # and a borrow of the same storage spell it alike.
    holders, storage = naming.holders, naming.storage

    def referent(ref: MIRReferent) -> str:
        if static_referent(ref):
            return "static"
        return _spell(ref.place, storage) or f"%{ref.place.root.index}"

    events = analyses.events
    modes = ({} if isinstance(events, MIRNotCovered)
             else {point: stmt.storage_write.mode for point, stmt in events.writes.items()})
    blocks = {block.id: block for block in fn.blocks}
    lines: set[int] = set()
    last: dict[tuple[int, MIRBlockId], int] = {}
    writes: dict[tuple[int, str], list[MIRLineWrite]] = {}
    replaced: dict[tuple[int, str], list[str]] = {}
    for block in fn.blocks:
        for index, stmt in enumerate(block.statements):
            if stmt.loc is None:
                continue
            line = stmt.loc.line
            lines.add(line)
            last[(line, block.id)] = index
            target = statement_target(stmt)
            if target is None:
                continue
            if (spelled := _spell(target, holders)) is not None:
                writes.setdefault((line, spelled), []).append(_line_write(
                    block.id, target, stmt.value if isinstance(stmt, MIRAssign) else None, slots, records))
            mode = modes.get(MIRPoint(block.id, index))
            if (mode is not None and mode is not MIRStorageWriteMode.INITIALIZE
                    and (spelled := _spell(target, storage)) is not None):
                replaced.setdefault((line, spelled), []).append(mode.name.lower())

    if not isinstance(events, MIRNotCovered):
        # A call's writes and a move out of owned storage are write events of
        # their line too. A call writing through an unnamed argument (a
        # borrow of `self.items`) is spelled by what that argument borrows.
        dependencies = analyses.dependencies
        others = [(point, place, "call") for point, places in events.call_writes.items() for place in places]
        others += [(point, place, "move") for point, place in events.moves.items()]
        for point, place, kind in others:
            loc = blocks[point.block].statements[point.index].loc
            if loc is None:
                continue
            spelled = {_spell(place, storage)} - {None}
            if not spelled and not isinstance(dependencies, MIRNotCovered) and point in dependencies.referents:
                spelled = {_spell(ref.place, storage) for ref in
                           resolve_referents(place, dependencies.referents[point], slots, records)} - {None}
            for text in spelled:
                replaced.setdefault((loc.line, text), []).append(kind)

    dependencies = analyses.dependencies
    leaves: dict[str, list[MIRPlace]] = {}
    # A named record holder's view members (`t.s`): the loans the objects it
    # reaches store, resolved through the holder (their storage is unnamed).
    members: dict[str, MIRPlace] = {}
    borrows: dict[tuple[int, str], list[MIRLineReferents]] = {}
    if not isinstance(dependencies, MIRNotCovered):
        layouts = fn.record_layouts
        for slot in fn.slots:
            if slot.id not in named:
                continue
            for leaf in _leaves(slot, slots):
                leaves.setdefault(named[slot.id], []).append(leaf)
                if leaf.projections:
                    leaves.setdefault(_spell(leaf, holders), []).append(leaf)
            if slot.shape.borrowed_aggregate:
                for member in view_members(layouts.get(slot.type)):
                    place = MIRPlace(slot.id, (*object_prefix(slot.shape), member))
                    members[_spell(place, holders)] = place
        for (line, block_id), index in last.items():
            state = dependencies.referents.get(MIRPoint(block_id, index + 1))
            if state is None:
                continue  # an unreachable block has no dependency state
            for spelled, held in leaves.items():
                refs = frozenset(referent(ref) for leaf in held for ref in state.get(leaf, ()))
                borrows.setdefault((line, spelled), []).append(MIRLineReferents(block_id.index, refs))
            for spelled, place in members.items():
                try:
                    loans = resolve_referents(place, state, slots, layouts) if state.get(MIRPlace(place.root)) else ()
                except MIRUnseededLoan:
                    loans = ()
                borrows.setdefault((line, spelled), []).append(
                    MIRLineReferents(block_id.index, frozenset(referent(ref) for ref in loans)))
    return MIRLineFacts(
        verdict.body, names, frozenset(leaves) | frozenset(members), frozenset(lines),
        {key: tuple(found) for key, found in writes.items()},
        {key: tuple(found) for key, found in replaced.items()},
        {key: tuple(found) for key, found in borrows.items()},
        borrows_gap=dependencies.reason if isinstance(dependencies, MIRNotCovered) else None,
        events_gap=events.reason if isinstance(events, MIRNotCovered) else None,
        ambiguous=MappingProxyType({bare: tuple(sorted(found)) for bare, found in _spell.ambiguous.items()}))


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
        lines.append(dump_liveness(analyses.liveness) if isinstance(analyses.liveness, MIRLiveness)
                     else f"liveness not covered: {analyses.liveness.reason}\n")
        lines.append(dump_scope_ends(analyses.scope.ends))
        lines.append(dump_scope_inspection(analyses.scope))
        lines.append(dump_dependencies(analyses.dependencies))
        lines.append(dump_call_effects(analyses.effects))
        lines.append(dump_storage(analyses.events))
        lines.append(dump_retention(analyses.retention, analyses.obligations))
        lines.append(dump_payload_ends(analyses.payload.ends))
        lines.append(dump_payload_inspection(analyses.payload))
        lines.append(dump_escapes(analyses.escapes, analyses.store_escapes))
        lines.append(dump_witness_cap(analyses.witnesses))
    return "\n".join(lines) if lines else "(no emitted bodies in this module)\n"
