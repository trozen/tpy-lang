"""
TurboPython Definite-Assignment Tracker

Tracks which variables are guaranteed to be initialized on all paths
reaching the current point. Prevents use-before-assignment errors that
would be undefined behavior in generated C++.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

from .context import BorrowKind
from .flow_facts import FlowFacts

if TYPE_CHECKING:
    from ..typesys import TpyType
    from .context import SemanticContext


def _borrows_from_frozen(
    triples: frozenset[tuple[str, str, BorrowKind]],
) -> tuple[dict[str, set[str]], dict[tuple[str, str], BorrowKind]]:
    """Convert frozen (storage, borrower, kind) triples back to mutable maps."""
    borrow_map: dict[str, set[str]] = {}
    kind_map: dict[tuple[str, str], BorrowKind] = {}
    for storage, borrower, kind in triples:
        borrow_map.setdefault(storage, set()).add(borrower)
        kind_map[(storage, borrower)] = kind
    return borrow_map, kind_map


class InitTracker:
    """Definite-assignment and rvalue-vars flow analysis."""

    def __init__(self, ctx: SemanticContext):
        self.ctx = ctx

    def save(self) -> FlowFacts:
        return FlowFacts(
            definitely_assigned=frozenset(self.ctx.definitely_assigned),
            init_terminated=self.ctx.init_terminated,
            rvalue_vars=frozenset(self.ctx.rvalue_vars),
            param_provenance_vars=frozenset(self.ctx.param_provenance_vars),
            non_null_ptr_vars=frozenset(self.ctx.non_null_ptr_vars),
            narrowed_types=frozenset(self.ctx.narrowed_types.items()),
            consumed_vars=frozenset(self.ctx.consumed_vars),
            borrows=frozenset(
                (storage, borrower, self.ctx.borrow_kinds.get((storage, borrower), BorrowKind.ALIAS))
                for storage, borrowers in self.ctx.borrows.items()
                for borrower in borrowers
            ),
        )

    def restore(self, state: FlowFacts) -> None:
        self.ctx.definitely_assigned = set(state.definitely_assigned)
        self.ctx.init_terminated = state.init_terminated
        self.ctx.rvalue_vars = set(state.rvalue_vars)
        self.ctx.param_provenance_vars = set(state.param_provenance_vars)
        self.ctx.non_null_ptr_vars = set(state.non_null_ptr_vars)
        self.ctx.narrowed_types = dict(state.narrowed_types)
        self.ctx.consumed_vars = set(state.consumed_vars)
        self.ctx.borrows, self.ctx.borrow_kinds = _borrows_from_frozen(state.borrows)

    def mark_assigned(self, name: str) -> None:
        self.ctx.definitely_assigned.add(name)

    def mark_terminated(self) -> None:
        self.ctx.init_terminated = True

    def mark_provenance(self, name: str, is_param_derived: bool) -> None:
        """Track whether a variable's storage derives from parameters/globals."""
        if is_param_derived:
            self.ctx.param_provenance_vars.add(name)
        else:
            self.ctx.param_provenance_vars.discard(name)

    def mark_non_null_ptr(self, name: str, is_non_null: bool) -> None:
        """Track whether a pointer variable is provably non-null."""
        if is_non_null:
            self.ctx.non_null_ptr_vars.add(name)
        else:
            self.ctx.non_null_ptr_vars.discard(name)

    def add_loop_var_provenance(self, name: str) -> None:
        self.ctx.param_provenance_vars.add(name)

    def remove_loop_var_provenance(self, name: str) -> None:
        self.ctx.param_provenance_vars.discard(name)

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
        self.ctx.definitely_assigned = set(before.definitely_assigned)
        self.ctx.init_terminated = False
        self.ctx.rvalue_vars = set(before.rvalue_vars)
        self.ctx.param_provenance_vars = set(before.param_provenance_vars)
        self.ctx.non_null_ptr_vars = set(before.non_null_ptr_vars)
        self.ctx.narrowed_types = dict(before.narrowed_types)
        self.ctx.consumed_vars = set(before.consumed_vars)
        self.ctx.borrows, self.ctx.borrow_kinds = _borrows_from_frozen(before.borrows)
        if condition_type_facts is not None:
            self.ctx.narrowed_types.update(condition_type_facts)

    def merge_branches(self, then_state: FlowFacts, else_state: FlowFacts) -> None:
        self.restore(FlowFacts.merge(then_state, else_state))
