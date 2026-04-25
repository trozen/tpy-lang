"""
TurboPython Definite-Assignment Tracker

Tracks which variables are guaranteed to be initialized on all paths
reaching the current point. Prevents use-before-assignment errors that
would be undefined behavior in generated C++.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

from .flow_facts import FlowFacts

if TYPE_CHECKING:
    from ..typesys import TpyType
    from .context import SemanticContext




class InitTracker:
    """Definite-assignment and rvalue-vars flow analysis."""

    def __init__(self, ctx: SemanticContext):
        self.ctx = ctx

    def save(self) -> FlowFacts:
        return FlowFacts(
            definitely_assigned=frozenset(self.ctx.func.definitely_assigned),
            init_terminated=self.ctx.func.init_terminated,
            rvalue_vars=frozenset(self.ctx.func.rvalue_vars),
            param_provenance_vars=frozenset(self.ctx.func.param_provenance_vars),
            safe_to_return_vars=frozenset(self.ctx.func.safe_to_return_vars),
            non_null_ptr_vars=frozenset(self.ctx.func.non_null_ptr_vars),
            narrowed_types=frozenset(self.ctx.func.narrowed_types.items()),
            consumed_vars=frozenset(self.ctx.func.consumed_vars),
            borrows=self.ctx.func.borrow_tracker.freeze(),
            value_ranges=frozenset(self.ctx.func.value_ranges.items()),
        )

    def restore(self, state: FlowFacts) -> None:
        self.ctx.func.definitely_assigned = set(state.definitely_assigned)
        self.ctx.func.init_terminated = state.init_terminated
        self.ctx.func.rvalue_vars = set(state.rvalue_vars)
        self.ctx.func.param_provenance_vars = set(state.param_provenance_vars)
        self.ctx.func.safe_to_return_vars = set(state.safe_to_return_vars)
        self.ctx.func.non_null_ptr_vars = set(state.non_null_ptr_vars)
        # Invariant: pending post-access ptr-narrowing flushes at statement
        # boundary (see `analyze_stmt` try/finally). Any entries surviving a
        # restore would leak into a branch they don't belong to.
        self.ctx.func.pending_non_null_ptr_vars.clear()
        self.ctx.func.narrowed_types = dict(state.narrowed_types)
        self.ctx.func.consumed_vars = set(state.consumed_vars)
        self.ctx.func.borrow_tracker.restore_from_frozen(state.borrows)
        self.ctx.func.value_ranges = dict(state.value_ranges)

    def mark_assigned(self, name: str) -> None:
        self.ctx.func.definitely_assigned.add(name)

    def mark_terminated(self) -> None:
        self.ctx.func.init_terminated = True

    def mark_provenance(self, name: str, is_param_derived: bool) -> None:
        """Track whether a variable's storage derives from parameters/globals."""
        if is_param_derived:
            self.ctx.func.param_provenance_vars.add(name)
        else:
            self.ctx.func.param_provenance_vars.discard(name)

    def mark_safe_to_return(self, name: str, is_safe: bool) -> None:
        """Record that returning ``name`` after this binding is non-dangling.

        Membership is what is_dangling_return consults for locals, so callers
        must also keep this set a superset of param_provenance_vars (the
        merge invariant relied on by FlowFacts.merge / apply_loop_exit_facts).
        See is_safe_to_return_expr in compatibility.py for the source rule.
        """
        if is_safe:
            self.ctx.func.safe_to_return_vars.add(name)
        else:
            self.ctx.func.safe_to_return_vars.discard(name)

    def mark_non_null_ptr(self, name: str, is_non_null: bool) -> None:
        """Track whether a pointer variable is provably non-null."""
        if is_non_null:
            self.ctx.func.non_null_ptr_vars.add(name)
        else:
            self.ctx.func.non_null_ptr_vars.discard(name)

    def add_loop_var_provenance(self, name: str) -> None:
        # Both sets must move together: leaving safe_to_return_vars empty
        # while populating param_provenance_vars would let an intersecting
        # merge drop the var from safe_to_return_vars while keeping it in
        # param_provenance_vars, breaking the documented subset invariant.
        self.ctx.func.param_provenance_vars.add(name)
        self.ctx.func.safe_to_return_vars.add(name)

    def remove_loop_var_provenance(self, name: str) -> None:
        self.ctx.func.param_provenance_vars.discard(name)
        self.ctx.func.safe_to_return_vars.discard(name)

    def apply_loop_entry_facts(
        self,
        before: FlowFacts,
        condition_type_facts: dict[str, TpyType] | None = None,
    ) -> None:
        """Apply flow facts at loop body entry.

        Restores the state from just before the loop, so that outer
        narrowing proven by enclosing if-blocks is preserved inside the
        loop body. Any additional narrowing the loop condition itself
        proves (e.g. ``while x is not None``) is layered on top.
        """
        self.ctx.func.definitely_assigned = set(before.definitely_assigned)
        self.ctx.func.init_terminated = False
        self.ctx.func.rvalue_vars = set(before.rvalue_vars)
        self.ctx.func.param_provenance_vars = set(before.param_provenance_vars)
        self.ctx.func.safe_to_return_vars = set(before.safe_to_return_vars)
        self.ctx.func.non_null_ptr_vars = set(before.non_null_ptr_vars)
        self.ctx.func.narrowed_types = dict(before.narrowed_types)
        self.ctx.func.consumed_vars = set(before.consumed_vars)
        self.ctx.func.borrow_tracker.restore_from_frozen(before.borrows)
        self.ctx.func.value_ranges = dict(before.value_ranges)
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
        body_end_param_prov = frozenset(self.ctx.func.param_provenance_vars)
        body_end_safe_return = frozenset(self.ctx.func.safe_to_return_vars)
        body_end_narrowed = frozenset(self.ctx.func.narrowed_types.items())
        self.restore(before)
        self.ctx.func.non_null_ptr_vars &= body_end_nn_ptr
        self.ctx.func.param_provenance_vars &= body_end_param_prov
        self.ctx.func.safe_to_return_vars &= body_end_safe_return
        self.ctx.func.narrowed_types = dict(body_end_narrowed & before.narrowed_types)
