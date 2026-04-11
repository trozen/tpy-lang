"""
Phase 2 of parameter mutation inference (8a).

After sema collects local mutation facts (direct_mutated_params, call_edges)
per function, this pass builds an intra-module call graph and propagates
mutation facts transitively through it.
"""

from __future__ import annotations
from typing import Optional

from ..typesys import FunctionInfo, CONST_PARAMS_METHODS

# Methods that must never be inferred const regardless of body analysis.
_NEVER_INFER_CONST = frozenset({"__init__", "__del__"}) | CONST_PARAMS_METHODS


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
    """Compute mutated_params, structural_mutated_params, and self_mutated for a function whose callees are resolved."""
    result: set[int] = set(fi.direct_mutated_params or frozenset())
    struct_result: set[int] = set(fi.direct_structural_mutated_params or frozenset())
    self_mutated = bool(fi.direct_self_mutated)
    for edge in (fi.call_edges or []):
        callee_mp = edge.callee_fi.mutated_params
        callee_smp = edge.callee_fi.structural_mutated_params
        if callee_mp is None:
            # Unknown callee -- conservative: mark all flowing params as mutated.
            # Skip sentinel -1 (self passed as arg): unknown callees are often
            # ephemeral FIs (substituted generics, constructor wrappers) that
            # lack mutation facts but rarely mutate their args.  Self-mutation
            # through method receivers is tracked separately via receiver_is_self.
            for _callee_idx, caller_idx in edge.param_map.items():
                if caller_idx >= 0:
                    result.add(caller_idx)
            if edge.receiver_is_self:
                self_mutated = True
        else:
            for callee_idx, caller_idx in edge.param_map.items():
                if callee_idx in callee_mp:
                    result.add(caller_idx)
            # Propagate self-mutation: if callee mutates its self and is called
            # as self.method(), the caller also mutates self.
            if edge.receiver_is_self and edge.callee_fi.self_mutated:
                self_mutated = True
        # Structural mutation propagation: more specific than mutated_params.
        if callee_smp is not None:
            for callee_idx, caller_idx in edge.param_map.items():
                if callee_idx in callee_smp:
                    struct_result.add(caller_idx)
        elif callee_mp is None:
            # Unknown callee: conservative -- treat all flowing params as structurally mutated
            for _callee_idx, caller_idx in edge.param_map.items():
                if caller_idx >= 0:
                    struct_result.add(caller_idx)
    # Sentinel -1 means "self" was passed as a function argument and the
    # callee mutated that parameter.  Convert to self_mutated flag.
    if -1 in result:
        self_mutated = True
        result.discard(-1)
    if -1 in struct_result:
        self_mutated = True
        struct_result.discard(-1)
    fi.mutated_params = frozenset(result)
    fi.structural_mutated_params = frozenset(struct_result)
    fi.self_mutated = self_mutated


def _resolve_cycle(cycle_fis: list[FunctionInfo]) -> None:
    """Resolve mutation facts for functions in cycles via fixed-point iteration.

    Both mutated_params and self_mutated lattices are monotone (only grow),
    so convergence is guaranteed. We bound iterations as a safety measure.
    structural_mutated_params is propagated in parallel.
    """
    # Initialize with direct mutations
    for fi in cycle_fis:
        fi.mutated_params = frozenset(fi.direct_mutated_params or frozenset())
        fi.structural_mutated_params = frozenset(fi.direct_structural_mutated_params or frozenset())
        fi.self_mutated = bool(fi.direct_self_mutated)

    # Safety bound: each iteration must add at least one param/self-mutation somewhere.
    max_iters = sum(len(fi.params) + 1 for fi in cycle_fis) + 1

    for _ in range(max_iters):
        changed = False
        for fi in cycle_fis:
            old_mp = fi.mutated_params
            old_smp = fi.structural_mutated_params
            old_sm = fi.self_mutated
            result: set[int] = set(fi.direct_mutated_params or frozenset())
            struct_result: set[int] = set(fi.direct_structural_mutated_params or frozenset())
            self_mutated = bool(fi.direct_self_mutated)
            for edge in (fi.call_edges or []):
                callee_mp = edge.callee_fi.mutated_params
                callee_smp = edge.callee_fi.structural_mutated_params
                if callee_mp is None:
                    for _callee_idx, caller_idx in edge.param_map.items():
                        if caller_idx >= 0:
                            result.add(caller_idx)
                    if edge.receiver_is_self:
                        self_mutated = True
                else:
                    for callee_idx, caller_idx in edge.param_map.items():
                        if callee_idx in callee_mp:
                            result.add(caller_idx)
                    if edge.receiver_is_self and edge.callee_fi.self_mutated:
                        self_mutated = True
                if callee_smp is not None:
                    for callee_idx, caller_idx in edge.param_map.items():
                        if callee_idx in callee_smp:
                            struct_result.add(caller_idx)
                elif callee_mp is None:
                    for _callee_idx, caller_idx in edge.param_map.items():
                        if caller_idx >= 0:
                            struct_result.add(caller_idx)
            # Sentinel -1 means "self" was passed as a function argument
            if -1 in result:
                self_mutated = True
                result.discard(-1)
            if -1 in struct_result:
                self_mutated = True
                struct_result.discard(-1)
            new_mp = frozenset(result)
            new_smp = frozenset(struct_result)
            if new_mp != old_mp or new_smp != old_smp or self_mutated != old_sm:
                fi.mutated_params = new_mp
                fi.structural_mutated_params = new_smp
                fi.self_mutated = self_mutated
                changed = True
        if not changed:
            break
    else:
        # Did not converge -- conservatively mark all params/self flowing through
        # unresolved edges
        for fi in cycle_fis:
            result = set(fi.mutated_params or frozenset())
            struct_result = set(fi.structural_mutated_params or frozenset())
            for edge in (fi.call_edges or []):
                if edge.callee_fi.mutated_params is None:
                    for _callee_idx, caller_idx in edge.param_map.items():
                        if caller_idx >= 0:
                            result.add(caller_idx)
                            struct_result.add(caller_idx)
                    if edge.receiver_is_self:
                        fi.self_mutated = True
            # Defensive: -1 shouldn't appear here (filtered by >= 0 above
            # and cleaned in prior iterations), but handle consistently.
            if -1 in result:
                fi.self_mutated = True
                result.discard(-1)
            if -1 in struct_result:
                fi.self_mutated = True
                struct_result.discard(-1)
            fi.mutated_params = frozenset(result)
            fi.structural_mutated_params = frozenset(struct_result)


def infer_method_const(all_fis: list[FunctionInfo]) -> None:
    """Back-propagate const inference: set is_readonly=True for methods proven non-self-mutating.

    A method is eligible for const inference when:
    - It is a method (is_method=True) but not a staticmethod.
    - It is not already readonly (explicit @readonly or built-in).
    - It is not in _NEVER_INFER_CONST (constructors, in-place operators).
    - It is not a consuming method (Own[Self] receiver).
    - Phase 1 + Phase 2 determined self_mutated=False.
    - The return value does not borrow from self (return_borrows_from does not contain -1).

    The last condition prevents the compiler from silently changing "-> T" (mutable ref)
    to "const T&" behind the user's back. If a method returns a reference into self's
    storage, making it const overrides the user's declared mutable return type.

    Setting is_readonly=True reuses all existing checks: codegen emits `const`,
    readonly-receiver enforcement passes, protocol conformance passes unchanged.
    """
    for fi in all_fis:
        if not fi.is_method or fi.is_staticmethod:
            continue
        if fi.is_readonly:
            continue
        if fi.is_consuming:
            continue
        if fi.name in _NEVER_INFER_CONST:
            continue
        if fi.direct_self_mutated is None:
            # Phase 1 facts not collected (should not happen for local methods)
            continue
        if fi.return_borrows_from is not None and -1 in fi.return_borrows_from:
            # Return value borrows from self's storage -- auto-const would change
            # the return from T& to const T&, overriding the user's declared type.
            continue
        if not fi.self_mutated:
            fi.is_readonly = True
