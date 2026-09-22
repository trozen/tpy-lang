"""
Phase 2 of parameter mutation inference (8a).

After sema collects local mutation facts (direct_mutated_params, call_edges)
per function, this pass builds an intra-module call graph and propagates
mutation facts transitively through it.
"""

from __future__ import annotations
from typing import Optional

from ..typesys import (
    FunctionInfo, MutationCallEdge, CONST_PARAMS_METHODS,
    recorded_return_borrow_sources, view_is_inherently_const,
    is_bodyless_binding,
)

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


class _Facts:
    """One function's mutation facts folded over its direct marks and its
    call edges: the three lattices (`mutated`, `structural`, `elem`) and the
    self flag, each monotone in the callees' facts."""
    __slots__ = ("mutated", "structural", "elem", "self_mutated")

    def __init__(self, fi: FunctionInfo, *, from_resolved: bool = False):
        # A cycle's fixed point refolds from the CURRENT estimate; a single
        # function (and a fresh cycle iteration) starts from the direct marks.
        if from_resolved:
            self.mutated = set(fi.mutated_params or frozenset())
            self.structural = set(fi.structural_mutated_params or frozenset())
            self.elem = set(fi.elem_mutated_params or frozenset())
        else:
            self.mutated = set(fi.direct_mutated_params or frozenset())
            self.structural = set(fi.direct_structural_mutated_params or frozenset())
            self.elem = set(fi.direct_elem_mutated_params or frozenset())
        self.self_mutated = bool(fi.direct_self_mutated)

    def fold_edge(self, edge: MutationCallEdge) -> None:
        callee = edge.callee_fi
        callee_mp = callee.mutated_params
        callee_smp = callee.structural_mutated_params
        callee_emp = callee.elem_mutated_params
        if callee_mp is None:
            # Unknown callee -- conservative: every flowing param is mutated
            # in every way. Except a lent slot at a BODY-LESS callee: with no
            # body there is nothing that writes through what the slot lends
            # (`next(it)` advances the handle, it does not touch `xs`); a
            # `@native` that wrote through an element it was lent would be
            # a runtime lying about itself, like one that ignored `@readonly`.
            stub = is_bodyless_binding(callee)
            for callee_idx, caller_idx in edge.param_map.items():
                if stub and callee_idx in edge.lent:
                    continue
                self.mutated.add(caller_idx)
                self.elem.add(caller_idx)
            if edge.receiver_is_self:
                self.self_mutated = True
        else:
            for callee_idx, caller_idx in edge.param_map.items():
                # A param bound through a lending call reaches the caller's
                # storage only through what the callee writes THROUGH it;
                # what the callee does to the param itself (advances the
                # combinator) touches nothing the caller passed.
                if callee_idx in edge.lent:
                    if callee_emp is not None and callee_idx in callee_emp:
                        self.mutated.add(caller_idx)
                        self.elem.add(caller_idx)
                    continue
                if callee_idx in callee_mp:
                    self.mutated.add(caller_idx)
                if callee_emp is not None and callee_idx in callee_emp:
                    self.elem.add(caller_idx)
            # Propagate self-mutation: if callee mutates its self and is called
            # as self.method(), the caller also mutates self.
            if edge.receiver_is_self and callee.self_mutated:
                self.self_mutated = True
        # Structural mutation propagation: more specific than mutated_params.
        if callee_smp is not None:
            for callee_idx, caller_idx in edge.param_map.items():
                if callee_idx in callee_smp and callee_idx not in edge.lent:
                    self.structural.add(caller_idx)
        elif callee_mp is None:
            # Unknown callee: conservative -- treat all flowing params as structurally mutated
            stub = is_bodyless_binding(callee)
            for callee_idx, caller_idx in edge.param_map.items():
                if not (stub and callee_idx in edge.lent):
                    self.structural.add(caller_idx)

    def store(self, fi: FunctionInfo) -> bool:
        """Write the facts to `fi`; True when any of them changed."""
        # Sentinel -1 means "self" was passed as a function argument and the
        # callee mutated that parameter.  Convert to self_mutated flag.
        for lattice in (self.mutated, self.structural, self.elem):
            if -1 in lattice:
                self.self_mutated = True
                lattice.discard(-1)
        new = (frozenset(self.mutated), frozenset(self.structural),
               frozenset(self.elem), self.self_mutated)
        old = (fi.mutated_params, fi.structural_mutated_params,
               fi.elem_mutated_params, fi.self_mutated)
        (fi.mutated_params, fi.structural_mutated_params,
         fi.elem_mutated_params, fi.self_mutated) = new
        return new != old


def _resolve_single(fi: FunctionInfo) -> None:
    """Compute the mutation facts for a function whose callees are resolved."""
    facts = _Facts(fi)
    for edge in (fi.call_edges or []):
        facts.fold_edge(edge)
    facts.store(fi)


def _resolve_cycle(cycle_fis: list[FunctionInfo]) -> None:
    """Resolve mutation facts for functions in cycles via fixed-point iteration.

    Every lattice is monotone (only grows), so convergence is guaranteed. We
    bound iterations as a safety measure.
    """
    # Initialize with direct mutations
    for fi in cycle_fis:
        _Facts(fi).store(fi)

    # Safety bound: each iteration must add at least one param/self-mutation somewhere.
    max_iters = sum(len(fi.params) + 1 for fi in cycle_fis) + 1

    for _ in range(max_iters):
        changed = False
        for fi in cycle_fis:
            facts = _Facts(fi)
            for edge in (fi.call_edges or []):
                facts.fold_edge(edge)
            if facts.store(fi):
                changed = True
        if not changed:
            break
    else:
        # Did not converge -- conservatively mark all params/self flowing through
        # unresolved edges
        for fi in cycle_fis:
            facts = _Facts(fi, from_resolved=True)
            facts.self_mutated = bool(fi.self_mutated)
            for edge in (fi.call_edges or []):
                if edge.callee_fi.mutated_params is None:
                    for _callee_idx, caller_idx in edge.param_map.items():
                        facts.mutated.add(caller_idx)
                        facts.structural.add(caller_idx)
                        facts.elem.add(caller_idx)
                    if edge.receiver_is_self:
                        facts.self_mutated = True
            facts.store(fi)


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
        if fi.is_auto_readonly_mutable_clone:
            # The const sibling is already skipped by the is_readonly check
            # above. Flipping the mutable clone too would give the pair two
            # identical signatures and break overload resolution.
            continue
        if fi.name in _NEVER_INFER_CONST:
            continue
        if fi.direct_self_mutated is None:
            # Phase 1 facts not collected (should not happen for local methods)
            continue
        if -1 in recorded_return_borrow_sources(fi):
            # Return value borrows from self's storage -- auto-const would change
            # the return from T& to const T&, overriding the user's declared type.
            # Inherently-const views carry no mutable alias, so auto-const is safe.
            if not view_is_inherently_const(fi.return_type):
                continue
        if not fi.self_mutated:
            fi.is_readonly = True
            fi.readonly_inferred = True
