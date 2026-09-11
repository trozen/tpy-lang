"""
TurboPython Definite-Assignment Tracker

Tracks which variables are guaranteed to be initialized on all paths
reaching the current point. Prevents use-before-assignment errors that
would be undefined behavior in generated C++.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

from .context import ITER_BORROWER
from .flow_facts import (
    FlowFacts, merge_borrow_triples, merge_binding_provenance,
)
from ..prescan import FactKills, alias_group, deref_view_key

if TYPE_CHECKING:
    from ..typesys import TpyType
    from .context import LoanInfo, SemanticContext
    from .narrowing import NarrowingTracker




class InitTracker:
    """Definite-assignment and rvalue-vars flow analysis."""

    def __init__(self, ctx: SemanticContext, narrowing: NarrowingTracker):
        self.ctx = ctx
        self.narrowing = narrowing

    def save(self) -> FlowFacts:
        return FlowFacts(
            definitely_assigned=frozenset(self.ctx.func.definitely_assigned),
            init_terminated=self.ctx.func.init_terminated,
            rvalue_vars=frozenset(self.ctx.func.rvalue_vars),
            non_null_ptr_vars=frozenset(self.ctx.func.non_null_ptr_vars),
            narrowed_types=frozenset(self.ctx.func.narrowed_types.items()),
            consumed_vars=frozenset(self.ctx.func.consumed_vars),
            binding_provenance=frozenset(self.ctx.func.binding_provenance.items()),
            borrows=self.ctx.func.borrow_tracker.freeze(),
            slot_resident=frozenset(self.ctx.func.borrow_tracker.slot_resident),
            value_ranges=frozenset(self.ctx.func.value_ranges.items()),
        )

    def restore(self, state: FlowFacts) -> None:
        self.ctx.func.definitely_assigned = set(state.definitely_assigned)
        self.ctx.func.init_terminated = state.init_terminated
        self.ctx.func.rvalue_vars = set(state.rvalue_vars)
        self.ctx.func.non_null_ptr_vars = set(state.non_null_ptr_vars)
        # Invariant: pending post-access ptr-narrowing flushes at statement
        # boundary (see `analyze_stmt` try/finally). Any entries surviving a
        # restore would leak into a branch they don't belong to.
        self.ctx.func.pending_non_null_ptr_vars.clear()
        self.ctx.func.narrowed_types = dict(state.narrowed_types)
        self.ctx.func.consumed_vars = set(state.consumed_vars)
        self.ctx.func.binding_provenance = dict(state.binding_provenance)
        self.ctx.func.borrow_tracker.restore_from_frozen(
            state.borrows, state.slot_resident)
        self.ctx.func.value_ranges = dict(state.value_ranges)

    def mark_assigned(self, name: str) -> None:
        self.ctx.func.definitely_assigned.add(name)

    def mark_terminated(self) -> None:
        self.ctx.func.init_terminated = True

    def mark_provenance(self, name: str, is_param_derived: bool) -> None:
        """Track whether a variable's storage derives from parameters/globals."""
        self.ctx.func.bp_set_param_derived(name, is_param_derived)

    def mark_safe_to_return(self, name: str, is_safe: bool) -> None:
        """Record that returning ``name`` after this binding is non-dangling.

        This is what is_dangling_return consults for locals; param_derived is
        a subset (see BindingProvenance / is_safe_to_return_expr).
        """
        self.ctx.func.bp_set_safe_to_return(name, is_safe)

    def mark_non_null_ptr(self, name: str, is_non_null: bool) -> None:
        """Track whether a pointer variable is provably non-null."""
        if is_non_null:
            self.ctx.func.non_null_ptr_vars.add(name)
        else:
            self.ctx.func.non_null_ptr_vars.discard(name)

    def add_loop_var_provenance(self, name: str) -> None:
        self.ctx.func.bp_add_loop_var_provenance(name)

    def remove_loop_var_provenance(self, name: str) -> None:
        self.ctx.func.bp_remove_loop_var_provenance(name)

    def apply_fact_kills(self, kills: FactKills) -> None:
        """Drop check-elision facts the given kill-set may invalidate.

        Touches only the UB-driving fact families (type narrowing, ptr
        non-null, value ranges); definite-assignment and the hazard sets
        are managed by their own merge policies.
        """
        f = self.ctx.func
        if kills.suspends:
            # The body suspends, so this meet crosses a suspension point
            # (back-edge re-entry, or handler/finally entry after a
            # mid-body suspension) -- everything a suspension kills must
            # die here too, or the restore resurrects it.
            self.narrowing.invalidate_suspension_facts()

        def _sweep(killed_key: str, kill_self: bool) -> None:
            prefix = killed_key + "."
            for k in [k for k in f.narrowed_types
                      if k.startswith(prefix)
                      or (kill_self and (k == killed_key
                                         or k == deref_view_key(killed_key)))]:
                del f.narrowed_types[k]
            if not kill_self:
                # Contents may change behind the key; the deref-view payload
                # narrowing reaches through it, so it dies even when the
                # key's own narrowing survives.
                f.narrowed_types.pop(deref_view_key(killed_key), None)
            for k in [k for k in f.non_null_ptr_vars
                      if k.startswith(prefix) or (kill_self and k == killed_key)]:
                f.non_null_ptr_vars.discard(k)
            for k in [k for k in f.value_ranges
                      if k.startswith(prefix) or (kill_self and k == killed_key)]:
                del f.value_ranges[k]
            root = killed_key.split(".", 1)[0]
            for k in [k for k, v in f.value_ranges.items()
                      if v.hi_len_of in (killed_key, root)]:
                del f.value_ranges[k]

        # Rebinds (names) kill only the spelled name's facts -- rebinding one
        # alias does not touch the others. Mutations (paths, receivers) reach
        # the shared object, so they kill across the static alias group,
        # mirroring NarrowingTracker._invalidate_field_facts. Coverage is
        # deliberately a superset of the live path: _sweep also clears
        # dotted-key value ranges and deref-view keys under receivers, which
        # the live invalidation never keys today -- if dotted range facts are
        # ever added, extend the live path too or it under-invalidates.
        aliases = f.current_alias_sources
        for name in kills.names:
            _sweep(name, kill_self=True)
        for path in kills.paths:
            root, _, rest = path.partition(".")
            for alias_root in alias_group(aliases, root):
                _sweep(f"{alias_root}.{rest}" if rest else alias_root,
                       kill_self=True)
        for recv in kills.receivers:
            root, _, rest = recv.partition(".")
            for alias_root in alias_group(aliases, root):
                _sweep(f"{alias_root}.{rest}" if rest else alias_root,
                       kill_self=False)

    def apply_loop_entry_facts(
        self,
        before: FlowFacts,
        condition_type_facts: dict[str, TpyType] | None = None,
        kills: FactKills | None = None,
    ) -> None:
        """Apply flow facts at loop body entry.

        Restores the state from just before the loop, so that outer
        narrowing proven by enclosing if-blocks is preserved inside the
        loop body. The body's kill-set is then applied (the back-edge may
        re-enter the body after the facts were invalidated -- single-pass
        analysis has no fixpoint, so killed facts must not be assumed at
        entry). Any additional narrowing the loop condition itself proves
        (e.g. ``while x is not None``) is layered on top last, since the
        condition is re-proven on every iteration.
        """
        self.ctx.func.definitely_assigned = set(before.definitely_assigned)
        self.ctx.func.init_terminated = False
        self.ctx.func.rvalue_vars = set(before.rvalue_vars)
        self.ctx.func.non_null_ptr_vars = set(before.non_null_ptr_vars)
        self.ctx.func.narrowed_types = dict(before.narrowed_types)
        self.ctx.func.consumed_vars = set(before.consumed_vars)
        self.ctx.func.binding_provenance = dict(before.binding_provenance)
        self.ctx.func.borrow_tracker.restore_from_frozen(
            before.borrows, before.slot_resident)
        self.ctx.func.value_ranges = dict(before.value_ranges)
        if kills is not None:
            self.apply_fact_kills(kills)
        if condition_type_facts is not None:
            self.ctx.func.narrowed_types.update(condition_type_facts)

    def merge_branches(self, then_state: FlowFacts, else_state: FlowFacts) -> None:
        self.restore(FlowFacts.merge(then_state, else_state))

    def apply_loop_exit_facts(self, before: FlowFacts) -> None:
        """Apply post-loop state for a may-execute-zero-times loop.

        Call with live post-body state still in ``ctx.func.*``. Monotone
        kill-facts (pointer non-null, parameter provenance, safe-to-return,
        type narrowing) survive iff they held both before the loop AND at
        body exit, so a reassignment that cleared a fact is not undone by
        ``restore(before)``.

        Single-pass conservative approximation -- proper fixpoint belongs
        with the THIR/MIR migration (see ``docs/IR_DESIGN.md``).
        """
        body_end_nn_ptr = frozenset(self.ctx.func.non_null_ptr_vars)
        body_end_narrowed = frozenset(self.ctx.func.narrowed_types.items())
        body_end_bp = frozenset(self.ctx.func.binding_provenance.items())
        body_end_borrows = self.ctx.func.borrow_tracker.freeze()
        body_end_slot_resident = frozenset(
            self.ctx.func.borrow_tracker.slot_resident)
        self.restore(before)
        self.ctx.func.non_null_ptr_vars &= body_end_nn_ptr
        self.ctx.func.narrowed_types = dict(body_end_narrowed & before.narrowed_types)
        self._merge_loop_body_loans(before, body_end_borrows,
                                    body_end_slot_resident)
        # Same lattice as a both-arms-live branch join: MUST facts (return
        # safety) intersect so a fact cleared in the body is not reinstated;
        # HAZARD facts (tuple-member) union in so a hazard introduced in the
        # body survives the loop.
        self.ctx.func.binding_provenance = dict(merge_binding_provenance(
            before.binding_provenance, body_end_bp, False, False))

    def _merge_loop_body_loans(
        self,
        before: FlowFacts,
        body_end_borrows: frozenset[tuple[str, str, LoanInfo]],
        body_end_slot_resident: frozenset[str],
    ) -> None:
        """Carry the loans the loop body took past the loop's closing brace.

        A loan bound in the body is still held after the last iteration -- the
        holder reads it there -- so dropping it with ``restore(before)`` hides
        every hazard a post-loop statement poses to it. Same lattice as a
        both-arms-live join: the union, each field at its more dangerous value.

        Only the synthetic iterator holder is dropped. It never expires (the
        loop does not remove it), so carrying it out would warn on every
        post-loop container mutation. Every other loan names real locals,
        which are function-scoped here -- a holder the body bound is readable
        after the loop -- and liveness kills the ones nothing reads there.
        """
        surviving = frozenset(
            triple for triple in body_end_borrows
            if triple[1] != ITER_BORROWER
        )
        merged = merge_borrow_triples(before.borrows, surviving, False, False)
        bt = self.ctx.func.borrow_tracker
        # Residency unions like it does at a branch join: a name the body
        # rebound through its slot still sits there at the closing brace.
        bt.restore_from_frozen(
            merged, before.slot_resident | body_end_slot_resident)
