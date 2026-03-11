"""
Phase 2 of parameter mutation inference (8a).

After sema collects local mutation facts (direct_mutated_params, call_edges)
per function, this pass builds an intra-module call graph and propagates
mutation facts transitively through it.
"""

from __future__ import annotations
from typing import Optional

from ..typesys import FunctionInfo


def propagate_mutation_facts(functions: list[FunctionInfo]) -> None:
    """Propagate mutation facts through the intra-module call graph.

    For each function with Phase 1 facts (direct_mutated_params + call_edges),
    compute the final mutated_params by unioning direct mutations with
    transitive mutations from callees.

    Functions without Phase 1 facts (builtins, imports with already-resolved
    mutated_params) are treated as ground truth and not modified.
    """
    # Separate module-local functions (have Phase 1 facts) from external
    local_fis: list[FunctionInfo] = [
        fi for fi in functions if fi.direct_mutated_params is not None
    ]
    if not local_fis:
        return

    # Build identity set for fast membership check
    local_set = set(id(fi) for fi in local_fis)

    # Topological sort via Kahn's algorithm.
    # Adjacency: fi -> callees (from call_edges).
    # We want to process callees before callers, so edges go caller -> callee.
    in_degree: dict[int, int] = {id(fi): 0 for fi in local_fis}
    # callee_id -> list of callers
    reverse_adj: dict[int, list[FunctionInfo]] = {id(fi): [] for fi in local_fis}

    for fi in local_fis:
        seen_callees: set[int] = set()
        for edge in (fi.call_edges or []):
            cid = id(edge.callee_fi)
            if cid in local_set and cid not in seen_callees:
                seen_callees.add(cid)
                in_degree[id(fi)] += 1
                reverse_adj[cid].append(fi)

    # Start with leaf functions (no local callees)
    queue: list[FunctionInfo] = [fi for fi in local_fis if in_degree[id(fi)] == 0]
    order: list[FunctionInfo] = []

    while queue:
        fi = queue.pop()
        order.append(fi)
        for caller in reverse_adj.get(id(fi), []):
            in_degree[id(caller)] -= 1
            if in_degree[id(caller)] == 0:
                queue.append(caller)

    # Functions not in topo order are involved in cycles (or are acyclic
    # callers whose callees are in cycles -- their in-degree never reached
    # zero). Fixed-point iteration handles both cases correctly.
    ordered_set = set(id(fi) for fi in order)
    cycle_fis = [fi for fi in local_fis if id(fi) not in ordered_set]

    # Process ordered (acyclic) functions
    for fi in order:
        _resolve_single(fi)

    # Process cycle functions with fixed-point iteration
    if cycle_fis:
        _resolve_cycle(cycle_fis)

    # Clear call_edges -- no longer needed after propagation, and leaving
    # them on exported FunctionInfo would cause unnecessary deferrals in
    # cross-module callers.
    for fi in local_fis:
        fi.call_edges = None


def _resolve_single(fi: FunctionInfo) -> None:
    """Compute mutated_params for a function whose callees are already resolved."""
    result: set[int] = set(fi.direct_mutated_params or frozenset())
    for edge in (fi.call_edges or []):
        callee_mp = edge.callee_fi.mutated_params
        if callee_mp is None:
            # Unknown callee (cross-module cycle or unanalyzed) -- conservative:
            # mark all caller params that flow into this callee as mutated
            for _callee_idx, caller_idx in edge.param_map.items():
                result.add(caller_idx)
        else:
            for callee_idx, caller_idx in edge.param_map.items():
                if callee_idx in callee_mp:
                    result.add(caller_idx)
    fi.mutated_params = frozenset(result)


def _resolve_cycle(cycle_fis: list[FunctionInfo]) -> None:
    """Resolve mutation facts for functions in cycles via fixed-point iteration.

    The mutated_params lattice is monotone (sets only grow), so convergence
    is guaranteed. We bound iterations as a safety measure.
    """
    # Initialize with direct mutations
    for fi in cycle_fis:
        fi.mutated_params = frozenset(fi.direct_mutated_params or frozenset())

    # Safety bound: each iteration must add at least one param somewhere,
    # so total params is an upper bound on iterations.
    max_iters = sum(len(fi.params) for fi in cycle_fis) + 1

    for _ in range(max_iters):
        changed = False
        for fi in cycle_fis:
            old = fi.mutated_params
            result: set[int] = set(fi.direct_mutated_params or frozenset())
            for edge in (fi.call_edges or []):
                callee_mp = edge.callee_fi.mutated_params
                if callee_mp is None:
                    for _callee_idx, caller_idx in edge.param_map.items():
                        result.add(caller_idx)
                else:
                    for callee_idx, caller_idx in edge.param_map.items():
                        if callee_idx in callee_mp:
                            result.add(caller_idx)
            new = frozenset(result)
            if new != old:
                fi.mutated_params = new
                changed = True
        if not changed:
            break
    else:
        # Did not converge -- conservatively mark all params flowing through
        # unresolved edges
        for fi in cycle_fis:
            result = set(fi.mutated_params or frozenset())
            for edge in (fi.call_edges or []):
                if edge.callee_fi.mutated_params is None:
                    for _callee_idx, caller_idx in edge.param_map.items():
                        result.add(caller_idx)
            fi.mutated_params = frozenset(result)
