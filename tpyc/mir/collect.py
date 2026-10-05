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
from ..thir.scalar_leaves import owned_leaf, record_type, storage_leaf
from ..typesys import TpyType, unwrap_readonly
from .call_contract import MIRSummaryResult
from .call_effects import MIRCallEffects, analyze_call_effects, dump_call_effects
from .definitions import MIRDefinitions
from .dependencies import (
    MIRDependencies, MIRReferent, _leaves, analyze_dependencies, dump_dependencies, resolve_referents,
)
from .dump import dump_function
from .liveness import MIRLiveness, MIRPoint, analyze_liveness, dump_liveness
from .lower import lower_constructor, lower_function
from .nodes import (
    MIRAssign, MIRBlockId, MIRBodyId, MIRBodyKind, MIRBorrow, MIRContainerElements, MIRContainerStructure,
    MIRCopy, MIRDeref, MIRField, MIRFunction, MIRMemberInitMode, MIRNotCovered, MIROptionalPayload, MIRPlace,
    MIRSlot, MIRSlotId, MIRSlotKind, MIRTupleIndex, MIRUnionPayload, MIRValueKind,
    function_body_kind, statement_target,
)
from .payload_lifetime import (
    MIRPayloadInspection, dump_payload_ends, dump_payload_inspection, inspect_payload_lifetimes,
)
from .retention import INITIALIZING_WRITES, MIRRetention, analyze_retention, dump_retention, static_referent
from .scope_lifetime import MIRScopeInspection, dump_scope_ends, dump_scope_inspection, inspect_scope_lifetimes
from .storage import MIRStorageEvents, analyze_storage, dump_storage
from .storage_adapter import MIRStorageRequest, certify_thir_storage
from .storage_evidence import MIRStorageConflict, MIRStorageVerdict, analyze_return_escapes
from ..mir_workspace import MIRCallWorkspace


def _declaration_name(name: str, func: TpyFunction | None) -> str:
    if func is not None and func.loc is not None:
        return f"{name}@{func.loc.line}:{func.loc.column}"
    return name


def body_declaration(func: TpyFunction, owner: TpyType | None) -> str:
    """The declaration a function body is keyed by -- `Owner.name@line:col`
    for a method -- in the call workspace and in the verdict walk alike, so
    the walk reads the lowering the workspace cached."""
    return _declaration_name(f"{owner.name}.{func.name}" if owner is not None else func.name, func)


def call_definitions(ctx: CodeGenContext, module_name: str) -> tuple[tuple[MIRBodyId, THIRFunction], ...]:
    """Every body publishing a callee identity -- its definition and an
    `@auto_readonly` def's access twin -- under the id the verdict walk
    gives it: the clones of a def share one declaration, so the second
    lowered body is `#2` as in `enumerate_body_sources`. A cache entry is
    read back only for the very function it lowered (`MIRCallWorkspace.lowering`)."""
    seen: dict[str, int] = {}
    result: list[tuple[MIRBodyId, THIRFunction]] = []
    for node, fn in ctx.thir_functions.items():
        name = body_declaration(node, fn.receiver.type if fn.receiver else None)
        count = seen[name] = seen.get(name, 0) + 1
        if fn.resolved_callee is not None:
            result.append((MIRBodyId(module_name, name if count == 1 else f"{name}#{count}"), fn))
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
        if isinstance(self.source, THIRFunction) and self.source.resolved_callee is not None:
            return self.source.resolved_callee.identity
        return None


@dataclass(frozen=True, eq=False)
class MIRAnalyses:
    """The analyses the verdict runs over one lowered body (`--dump-mir`
    prints all but the return escapes)."""
    liveness: MIRLiveness
    dependencies: MIRDependencies | MIRNotCovered
    events: MIRStorageEvents | MIRNotCovered
    scope: MIRScopeInspection
    effects: MIRCallEffects | MIRNotCovered
    retention: MIRRetention | MIRNotCovered
    payload: MIRPayloadInspection
    escapes: tuple[MIRStorageConflict, ...] | MIRNotCovered

    @property
    def gaps(self) -> tuple[tuple[str, MIRNotCovered], ...]:
        staged = (("dependencies", self.dependencies), ("scope ends", self.scope.ends),
                  ("scope conflicts", self.scope.conflicts), ("payload ends", self.payload.ends),
                  ("payload conflicts", self.payload.conflicts), ("storage", self.events),
                  ("call effects", self.effects), ("retention", self.retention),
                  ("return escapes", self.escapes))
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
        if not isinstance(self.escapes, MIRNotCovered) and self.escapes:
            found.append("return_escape")
        return tuple(found)


def analyze_body(fn: MIRFunction) -> MIRAnalyses:
    liveness = analyze_liveness(fn)
    dependencies = analyze_dependencies(fn, liveness)
    events = analyze_storage(fn)
    return MIRAnalyses(liveness, dependencies, events, inspect_scope_lifetimes(fn),
                       analyze_call_effects(fn, dependencies),
                       analyze_retention(fn, liveness, dependencies, events), inspect_payload_lifetimes(fn),
                       analyze_return_escapes(fn, dependencies))


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
        refusal = refused("module initialization") if ctx.thir_top_level is not None else missing(module)
        yield MIRBodySource(identity("__tpy_init"), "__tpy_init", None, None, None, None, MIRBodyKind.MODULE,
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
    cached = workspace.lowering(source.body, source.source) if workspace is not None and not fresh else None
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


# --- line facts -------------------------------------------------------------------

_SELECTORS = {MIRSlotKind.PARAMETER: "param", MIRSlotKind.LOCAL: "local", MIRSlotKind.GLOBAL: "global"}


@dataclass(frozen=True)
class MIRLineWrite:
    """One statement on a source line writing a named place."""
    block: int
    # The written place's value kind: a slot's own, a wrapper member's, OWNED
    # for record or owned-leaf storage (a field, or what a holder points at),
    # SCALAR for an inert-leaf field; None for a place with neither.
    kind: MIRValueKind | None
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


def _spell(place: MIRPlace, roots: Mapping[MIRSlotId, str]) -> str | None:
    root = roots.get(place.root)
    if root is None:
        return None
    text = root
    for projection in place.projections:
        match projection:
            case MIRField(id=field):
                text += f".{field.name}"
            case MIRTupleIndex(index=index):
                text += f"[{index}]"
            case MIROptionalPayload():
                text += "[payload]"
            case MIRUnionPayload(alternative=alternative):
                text += f"[alt{alternative}]"
            case MIRContainerStructure():
                text += "[structure]"
            case MIRContainerElements():
                text += "[elements]"
            case MIRDeref():
                pass
    return text


def _written_kind(place: MIRPlace, slot: MIRSlot) -> MIRValueKind | None:
    match place.projections:
        case ():
            return slot.value_kind
        case (MIRTupleIndex(index=index),) if slot.tuple_layout is not None:
            return slot.tuple_layout.elements[index].kind
        case (MIROptionalPayload(),) if slot.optional_layout is not None:
            return slot.optional_layout.kind
        case (*_, MIRDeref()):
            # Whole storage a holder points at: storage_destination's replacement.
            return MIRValueKind.OWNED
        case (MIRContainerElements(),) if slot.container_layout is not None:
            # An element is the container's own storage unless it is an inert leaf.
            scalar = slot.container_layout.subscript.kind is MIRValueKind.SCALAR
            return MIRValueKind.SCALAR if scalar else MIRValueKind.OWNED
        case (*_, MIRField(type=field_type)):
            typ = unwrap_readonly(field_type)
            if owned_leaf(typ) or record_type(typ):
                return MIRValueKind.OWNED
            return MIRValueKind.SCALAR if storage_leaf(typ) else None
    return None


def line_facts(verdict: MIRBodyVerdict) -> MIRLineFacts | None:
    """The per-line facts of a lowered body, None for a body that did not
    lower. Reads the analyses the verdict already ran, never re-lowers; runs
    under the compilation (field kinds read its TypeDefs)."""
    fn, analyses = verdict.function, verdict.analyses
    if fn is None or analyses is None:
        return None
    slots = {slot.id: slot for slot in fn.slots}
    by_name: dict[str, dict[str, list[MIRSlotId]]] = {}
    for slot in fn.slots:
        selector = _SELECTORS.get(slot.kind)
        if selector is not None and slot.name is not None:
            by_name.setdefault(slot.name, {}).setdefault(selector, []).append(slot.id)
    names = {name: {selector: name if len(selectors) == 1 else f"{selector}({name})" for selector in selectors}
             for name, selectors in by_name.items()}
    named = {sid: names[name][selector]
             for name, selectors in by_name.items() for selector, ids in selectors.items() for sid in ids}
    bindings: dict[MIRSlotId, tuple[str, int | None]] = {}
    for block in fn.blocks:
        for stmt in block.statements:
            if (isinstance(stmt, MIRAssign) and isinstance(stmt.value, MIRBorrow)
                    and not stmt.value.source.projections and not stmt.target.projections
                    and stmt.target.root in named and stmt.value.source.root not in named
                    and stmt.value.source.root not in bindings):
                bindings[stmt.value.source.root] = (named[stmt.target.root], stmt.loc.line if stmt.loc else None)
    bound = [name for name, _line in bindings.values()]
    storage = dict(named)
    for root, (name, line) in bindings.items():
        # A name rebound to fresh storage names several objects; the binding line tells them apart.
        storage[root] = name if bound.count(name) == 1 else f"{name}@{line}"

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
            if (spelled := _spell(target, named)) is not None:
                copy = isinstance(stmt, MIRAssign) and isinstance(stmt.value, MIRCopy)
                writes.setdefault((line, spelled), []).append(
                    MIRLineWrite(block.id.index, _written_kind(target, slots[target.root]), copy))
            mode = modes.get(MIRPoint(block.id, index))
            if mode is not None and mode not in INITIALIZING_WRITES and (spelled := _spell(target, storage)) is not None:
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
                           resolve_referents(place, dependencies.referents[point], slots)} - {None}
            for text in spelled:
                replaced.setdefault((loc.line, text), []).append(kind)

    if fn.receiver_init is not None:
        # The receiver's entry initialization carries no CFG statement; its
        # members' source lines are the constructor's field writes.
        receiver = slots[fn.receiver_init.receiver]
        layout = next((r for r in fn.records if r.type == receiver.type), None)
        if layout is not None and receiver.id in named:
            for member, field in zip(fn.receiver_init.fields, layout.fields):
                if member.loc is None:
                    continue
                lines.add(member.loc.line)
                spelled = f"{named[receiver.id]}.{field.id.name}"
                kind = MIRValueKind.SCALAR if member.mode is MIRMemberInitMode.SCALAR else MIRValueKind.OWNED
                writes.setdefault((member.loc.line, spelled), []).append(
                    MIRLineWrite(fn.entry.index, kind, member.mode is MIRMemberInitMode.COPY))

    dependencies = analyses.dependencies
    leaves: dict[str, list[MIRPlace]] = {}
    borrows: dict[tuple[int, str], list[MIRLineReferents]] = {}
    if not isinstance(dependencies, MIRNotCovered):
        for slot in fn.slots:
            if slot.id not in named:
                continue
            for leaf in _leaves(slot):
                leaves.setdefault(named[slot.id], []).append(leaf)
                if leaf.projections:
                    leaves.setdefault(_spell(leaf, named), []).append(leaf)
        for (line, block_id), index in last.items():
            state = dependencies.referents.get(MIRPoint(block_id, index + 1))
            if state is None:
                continue  # an unreachable block has no dependency state
            for spelled, held in leaves.items():
                refs = frozenset(referent(ref) for leaf in held for ref in state.get(leaf, ()))
                borrows.setdefault((line, spelled), []).append(MIRLineReferents(block_id.index, refs))
    return MIRLineFacts(
        verdict.body, names, frozenset(leaves), frozenset(lines),
        {key: tuple(found) for key, found in writes.items()},
        {key: tuple(found) for key, found in replaced.items()},
        {key: tuple(found) for key, found in borrows.items()},
        borrows_gap=dependencies.reason if isinstance(dependencies, MIRNotCovered) else None,
        events_gap=events.reason if isinstance(events, MIRNotCovered) else None)


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
