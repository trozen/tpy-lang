"""Workspace scheduling of MIR evidence after emitted THIR has been collected."""

from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType

from .thir import nodes as th
from .thir.validate import _iter_children
from .mir.call_contract import MIRSummaryResult, MIRSummaryState
from .mir.definitions import MIRDefinitions
from .mir.lower import lower_function
from .mir.nodes import MIRBodyId, MIRFunction, MIRNotCovered
from .mir.summaries import summarize_function


@dataclass(frozen=True)
class MIRCallWorkspace:
    summaries: Mapping[th.THIRFunctionIdentity, MIRSummaryResult]
    bodies: Mapping[MIRBodyId, MIRFunction | MIRNotCovered]
    # Per identity, the body that defines it and the `@auto_readonly` access
    # twin publishing it beside the definition (lowered by the verdict walk).
    definitions: Mapping[th.THIRFunctionIdentity, tuple[MIRBodyId, th.THIRFunction]] = MappingProxyType({})
    twins: Mapping[th.THIRFunctionIdentity, tuple[MIRBodyId, th.THIRFunction]] = MappingProxyType({})

    def lowering(self, body: MIRBodyId, source: th.THIRFunction) -> MIRFunction | MIRNotCovered | None:
        """The cached lowering of exactly `source` under `body` -- a
        definition's, or its access twin's from the twin check; None for any
        other function, so a twin never reads its definition's MIR."""
        callee = source.resolved_callee
        if callee is None:
            return None
        owner = self.twins if source.access_twin else self.definitions
        cached = owner.get(callee.identity)
        if cached is None or cached[0] != body or cached[1] is not source:
            return None
        return self.bodies.get(body)


def _dependencies(fn: th.THIRFunction) -> set[th.THIRFunctionIdentity]:
    result: set[th.THIRFunctionIdentity] = set()
    pending = list(fn.body)
    while pending:
        node = pending.pop()
        if isinstance(node, (th.THIRCall, th.THIRMethodCall)) and node.resolved_callee is not None:
            result.add(node.resolved_callee.identity)
        pending.extend(_iter_children(node))
    return result


def _twin_checked(known: MIRSummaryResult, twin: tuple[MIRBodyId, th.THIRFunction], definitions: MIRDefinitions,
                  summaries: Mapping[th.THIRFunctionIdentity, MIRSummaryResult],
                  bodies: dict[MIRBodyId, MIRFunction | MIRNotCovered]) -> MIRSummaryResult:
    """The definition's summary, kept only when its access twin summarizes
    to the same contract: inside a clone a call may resolve by the
    receiver's access, so equal bodies are checked, never assumed. The
    twin's lowering is cached under its own body id."""
    body, fn = twin
    lowered = bodies[body] = lower_function(fn, body, definitions=definitions, summaries=summaries)
    other = None if isinstance(lowered, MIRNotCovered) else summarize_function(fn, lowered, definitions)
    if other is None or other.state is not MIRSummaryState.KNOWN or other.summary != known.summary:
        return MIRSummaryResult.opaque("twin bodies differ")
    return known


def analyze_call_workspace(functions: tuple[tuple[MIRBodyId, th.THIRFunction], ...],
                           definitions: MIRDefinitions) -> MIRCallWorkspace:
    """Publish leaves before callers; unresolved cycles never start empty."""
    declarations: dict[th.THIRFunctionIdentity, tuple[MIRBodyId, th.THIRFunction]] = {}
    summaries: dict[th.THIRFunctionIdentity, MIRSummaryResult] = {}
    bodies: dict[MIRBodyId, MIRFunction | MIRNotCovered] = {}
    twins: dict[th.THIRFunctionIdentity, tuple[MIRBodyId, th.THIRFunction]] = {}
    for body, fn in functions:
        if fn.resolved_callee is None:
            continue
        key = fn.resolved_callee.identity
        if fn.access_twin:
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
        deps = _dependencies(fn)
        if key in twins:
            deps |= _dependencies(twins[key][1])
        pending = {dep for dep in deps if dep in summaries
                   and summaries[dep].state is MIRSummaryState.PENDING}
        waiting[key] = len(pending)
        for dep in pending:
            callers.setdefault(dep, []).append(key)
    ready = deque(key for key, count in waiting.items() if count == 0)
    while ready:
        key = ready.popleft()
        body, fn = declarations[key]
        result = lower_function(fn, body,
                                definitions=definitions, summaries=MappingProxyType(summaries))
        bodies[body] = result
        summaries[key] = (MIRSummaryResult.opaque(result.reason) if isinstance(result, MIRNotCovered)
                          else summarize_function(fn, result, definitions))
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
