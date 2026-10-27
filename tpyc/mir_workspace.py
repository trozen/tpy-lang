"""Workspace scheduling of MIR evidence after emitted THIR has been collected."""

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from .thir import nodes as th
from .mir.call_contract import MIRSummaryResult, MIRSummaryState
from .mir.definitions import MIRDefinitions, body_calls, constructor_callee, constructor_function
from .mir.lower import MIRLoweredStorage, lower_callable, lower_function_storage
from .mir.nodes import MIRBodyId, MIRNotCovered
from .mir.summaries import summarize_function


# A body the workspace schedules: a function's, or a constructor's, which
# is the function of its parameters returning its record.
Callable = th.THIRFunction | th.THIRConstructor


def callable_identity(source: Callable) -> th.THIRFunctionIdentity | None:
    """The callee identity a body defines; None for one no call resolves to."""
    if isinstance(source, th.THIRConstructor):
        return constructor_callee(source).identity if source.record_layout is not None else None
    return source.resolved_callee.identity if source.resolved_callee is not None else None


def _summarize(source: Callable, lowered: MIRLoweredStorage, definitions: MIRDefinitions) -> MIRSummaryResult:
    declaration = constructor_function(source) if isinstance(source, th.THIRConstructor) else source
    return summarize_function(declaration, lowered.function, lowered.prepared, definitions)


@dataclass(frozen=True)
class MIRCallWorkspace:
    summaries: Mapping[th.THIRFunctionIdentity, MIRSummaryResult]
    # Each body as lowered, with its preparation.
    bodies: Mapping[MIRBodyId, MIRLoweredStorage | MIRNotCovered]
    # Per identity, the body that defines it and the `@auto_readonly` access
    # twin publishing it beside the definition (lowered by the verdict walk).
    definitions: Mapping[th.THIRFunctionIdentity, tuple[MIRBodyId, Callable]] = MappingProxyType({})
    twins: Mapping[th.THIRFunctionIdentity, tuple[MIRBodyId, th.THIRFunction]] = MappingProxyType({})

    def lowering(self, body: MIRBodyId, source: Callable) -> MIRLoweredStorage | MIRNotCovered | None:
        """The cached lowering of exactly `source` under `body` -- a
        definition's, or its access twin's from the twin check; None for any
        other function, so a twin never reads its definition's MIR."""
        identity = callable_identity(source)
        if identity is None:
            return None
        owner = self.twins if isinstance(source, th.THIRFunction) and source.access_twin else self.definitions
        cached = owner.get(identity)
        if cached is None or cached[0] != body or cached[1] is not source:
            return None
        return self.bodies.get(body)


def _dependencies(fn: Callable, definitions: MIRDefinitions) -> set[th.THIRFunctionIdentity]:
    """The callees a body's summary waits for: every callee it calls
    (`body_calls`), constructors included."""
    return {callee.identity for _call, callee in body_calls(fn, definitions)}


def _twin_checked(known: MIRSummaryResult, twin: tuple[MIRBodyId, th.THIRFunction], definitions: MIRDefinitions,
                  summaries: Mapping[th.THIRFunctionIdentity, MIRSummaryResult],
                  bodies: dict[MIRBodyId, MIRLoweredStorage | MIRNotCovered]) -> MIRSummaryResult:
    """The definition's summary, kept only when its access twin summarizes
    to the same contract: inside a clone a call may resolve by the
    receiver's access, so equal bodies are checked, never assumed. The
    twin's lowering is cached under its own body id."""
    body, fn = twin
    lowered = bodies[body] = lower_function_storage(fn, body, definitions=definitions, summaries=summaries)
    other = (None if isinstance(lowered, MIRNotCovered)
             else summarize_function(fn, lowered.function, lowered.prepared, definitions))
    if other is None or other.state is not MIRSummaryState.KNOWN or other.summary != known.summary:
        return MIRSummaryResult.opaque("twin bodies differ")
    return known


def analyze_call_workspace(functions: tuple[tuple[MIRBodyId, Callable], ...],
                           definitions: MIRDefinitions) -> MIRCallWorkspace:
    """Publish leaves before callers; unresolved cycles never start empty."""
    declarations: dict[th.THIRFunctionIdentity, tuple[MIRBodyId, Callable]] = {}
    summaries: dict[th.THIRFunctionIdentity, MIRSummaryResult] = {}
    bodies: dict[MIRBodyId, MIRLoweredStorage | MIRNotCovered] = {}
    twins: dict[th.THIRFunctionIdentity, tuple[MIRBodyId, th.THIRFunction]] = {}
    for body, fn in functions:
        key = callable_identity(fn)
        if key is None:
            continue
        if isinstance(fn, th.THIRFunction) and fn.access_twin:
            twins.setdefault(key, (body, fn))
            continue
        if key in summaries:
            summaries[key] = MIRSummaryResult.opaque("duplicate definition identity")
        else:
            declarations[key] = (body, fn)
            summaries[key] = MIRSummaryResult(MIRSummaryState.PENDING)
    waiting: dict[th.THIRFunctionIdentity, int] = {}
    callers: dict[th.THIRFunctionIdentity, list[th.THIRFunctionIdentity]] = {}
    for key, (_body, fn) in declarations.items():
        if summaries[key].state is not MIRSummaryState.PENDING:
            continue
        deps = _dependencies(fn, definitions)
        if key in twins:
            deps |= _dependencies(twins[key][1], definitions)
        pending = {dep for dep in deps if dep in summaries
                   and summaries[dep].state is MIRSummaryState.PENDING}
        waiting[key] = len(pending)
        for dep in pending:
            callers.setdefault(dep, []).append(key)
    ready = deque(key for key, count in waiting.items() if count == 0)
    while ready:
        key = ready.popleft()
        body, fn = declarations[key]
        result = lower_callable(fn, body, definitions, MappingProxyType(summaries))
        bodies[body] = result
        summaries[key] = (MIRSummaryResult.opaque(result.reason) if isinstance(result, MIRNotCovered)
                          else _summarize(fn, result, definitions))
        if key in twins and summaries[key].state is MIRSummaryState.KNOWN:
            summaries[key] = _twin_checked(summaries[key], twins[key], definitions, MappingProxyType(summaries),
                                           bodies)
        for caller in callers.get(key, ()):
            waiting[caller] -= 1
            if waiting[caller] == 0:
                ready.append(caller)
    for key, result in summaries.items():
        if result.state is MIRSummaryState.PENDING:
            summaries[key] = MIRSummaryResult.opaque("recursive or recursion-dependent call")
    return MIRCallWorkspace(MappingProxyType(summaries), MappingProxyType(bodies),
                            MappingProxyType(declarations), MappingProxyType(twins))


@dataclass(frozen=True)
class MIRProgram:
    """The workspace-wide MIR inputs every per-body lowering of one compilation shares."""
    definitions: MIRDefinitions
    workspace: MIRCallWorkspace
