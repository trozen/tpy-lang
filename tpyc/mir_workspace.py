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


def _dependencies(fn: th.THIRFunction) -> set[th.THIRFunctionIdentity]:
    result: set[th.THIRFunctionIdentity] = set()
    pending = list(fn.body)
    while pending:
        node = pending.pop()
        if isinstance(node, (th.THIRCall, th.THIRMethodCall)) and node.resolved_callee is not None:
            result.add(node.resolved_callee.identity)
        pending.extend(_iter_children(node))
    return result


def analyze_call_workspace(functions: tuple[tuple[MIRBodyId, th.THIRFunction], ...],
                           definitions: MIRDefinitions) -> MIRCallWorkspace:
    """Publish leaves before callers; unresolved cycles never start empty."""
    declarations: dict[th.THIRFunctionIdentity, tuple[MIRBodyId, th.THIRFunction]] = {}
    summaries: dict[th.THIRFunctionIdentity, MIRSummaryResult] = {}
    bodies: dict[MIRBodyId, MIRFunction | MIRNotCovered] = {}
    for body, fn in functions:
        if fn.resolved_callee is None:
            continue
        key = fn.resolved_callee.identity
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
        for caller in callers.get(key, ()):
            waiting[caller] -= 1
            if waiting[caller] == 0:
                ready.append(caller)
    for key, result in summaries.items():
        if result.state is MIRSummaryState.PENDING:
            summaries[key] = MIRSummaryResult.opaque("recursive or recursion-dependent call")
    return MIRCallWorkspace(MappingProxyType(summaries), MappingProxyType(bodies))


@dataclass(frozen=True)
class MIRProgram:
    """The workspace-wide MIR inputs every per-body lowering of one compilation shares."""
    definitions: MIRDefinitions
    workspace: MIRCallWorkspace
