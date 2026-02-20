"""
TurboPython Definite-Assignment Tracker

Tracks which variables are guaranteed to be initialized on all paths
reaching the current point. Prevents use-before-assignment errors that
would be undefined behavior in generated C++.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

from .narrowing import ExprIdentity

if TYPE_CHECKING:
    from ..typesys import TpyType
    from .context import SemanticContext

# (definitely_assigned, init_terminated, rvalue_vars, param_provenance_vars,
#  non_none_exprs, non_null_ptr_vars, narrowed_types)
FlowState = tuple[frozenset[str], bool, frozenset[str], frozenset[str],
                  frozenset[ExprIdentity], frozenset[str],
                  frozenset[tuple[str, 'TpyType']]]


class InitTracker:
    """Definite-assignment and rvalue-vars flow analysis."""

    def __init__(self, ctx: SemanticContext):
        self.ctx = ctx

    def save(self) -> FlowState:
        return (frozenset(self.ctx.definitely_assigned),
                self.ctx.init_terminated,
                frozenset(self.ctx.rvalue_vars),
                frozenset(self.ctx.param_provenance_vars),
                frozenset(self.ctx.non_none_exprs),
                frozenset(self.ctx.non_null_ptr_vars),
                frozenset(self.ctx.narrowed_types.items()))

    def restore(self, state: FlowState) -> None:
        self.ctx.definitely_assigned = set(state[0])
        self.ctx.init_terminated = state[1]
        self.ctx.rvalue_vars = set(state[2])
        self.ctx.param_provenance_vars = set(state[3])
        self.ctx.non_none_exprs = set(state[4])
        self.ctx.non_null_ptr_vars = set(state[5])
        self.ctx.narrowed_types = dict(state[6])

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
        before: FlowState,
        condition_type_facts: dict[str, TpyType] | None = None,
        condition_non_none_exprs: set[tuple[str, ...]] | None = None,
    ) -> None:
        """Apply conservative flow facts at loop body entry.

        We treat loop bodies as potentially revisited. To avoid stale
        proofs across iterations:
        - expression-identity facts default to cleared unless re-proven by loop
          condition facts for this iteration entry.
        - type narrowing facts are constrained to condition-proven facts.
        """
        self.ctx.definitely_assigned = set(before[0])
        self.ctx.init_terminated = False
        self.ctx.rvalue_vars = set(before[2])
        self.ctx.param_provenance_vars = set(before[3])
        if condition_non_none_exprs is None:
            self.ctx.non_none_exprs = set()
        else:
            self.ctx.non_none_exprs = set(condition_non_none_exprs)
        self.ctx.non_null_ptr_vars = set(before[5])
        if condition_type_facts is not None:
            self.ctx.narrowed_types = dict(condition_type_facts)
        else:
            # Restore from saved state (preserves narrowing proven before the loop)
            self.ctx.narrowed_types = dict(before[6])

    def merge_branches(self, then_state: FlowState, else_state: FlowState) -> None:
        then_assigned, then_term, then_rvalue, then_prov, then_non_none_exprs, then_nn_ptr, then_narrowed = then_state
        else_assigned, else_term, else_rvalue, else_prov, else_non_none_exprs, else_nn_ptr, else_narrowed = else_state
        if then_term and else_term:
            self.ctx.definitely_assigned = set(then_assigned | else_assigned)
            self.ctx.init_terminated = True
        elif then_term:
            self.ctx.definitely_assigned = set(else_assigned)
            self.ctx.init_terminated = False
        elif else_term:
            self.ctx.definitely_assigned = set(then_assigned)
            self.ctx.init_terminated = False
        else:
            self.ctx.definitely_assigned = set(then_assigned & else_assigned)
            self.ctx.init_terminated = False
        # Rvalue merge follows the same logic as definitely_assigned:
        # a terminated branch can't reach the code after the if-statement.
        if then_term and else_term:
            self.ctx.rvalue_vars = set(then_rvalue | else_rvalue)
        elif then_term:
            self.ctx.rvalue_vars = set(else_rvalue)
        elif else_term:
            self.ctx.rvalue_vars = set(then_rvalue)
        else:
            self.ctx.rvalue_vars = set(then_rvalue & else_rvalue)
        # Provenance merge: intersection when both branches reach the merge
        # point -- the variable is only param-derived if both paths agree.
        if then_term and else_term:
            self.ctx.param_provenance_vars = set(then_prov | else_prov)
        elif then_term:
            self.ctx.param_provenance_vars = set(else_prov)
        elif else_term:
            self.ctx.param_provenance_vars = set(then_prov)
        else:
            self.ctx.param_provenance_vars = set(then_prov & else_prov)
        if then_term and else_term:
            self.ctx.non_none_exprs = set(then_non_none_exprs | else_non_none_exprs)
        elif then_term:
            self.ctx.non_none_exprs = set(else_non_none_exprs)
        elif else_term:
            self.ctx.non_none_exprs = set(then_non_none_exprs)
        else:
            self.ctx.non_none_exprs = set(then_non_none_exprs & else_non_none_exprs)
        # Non-null pointer merge: intersection when both branches live.
        if then_term and else_term:
            self.ctx.non_null_ptr_vars = set(then_nn_ptr | else_nn_ptr)
        elif then_term:
            self.ctx.non_null_ptr_vars = set(else_nn_ptr)
        elif else_term:
            self.ctx.non_null_ptr_vars = set(then_nn_ptr)
        else:
            self.ctx.non_null_ptr_vars = set(then_nn_ptr & else_nn_ptr)
        # Type narrowing merge: intersection when both branches live.
        then_narrowed_dict = dict(then_narrowed)
        else_narrowed_dict = dict(else_narrowed)
        if then_term and else_term:
            merged = {**then_narrowed_dict, **else_narrowed_dict}
        elif then_term:
            merged = else_narrowed_dict
        elif else_term:
            merged = then_narrowed_dict
        else:
            merged = {k: v for k, v in then_narrowed_dict.items()
                      if k in else_narrowed_dict and else_narrowed_dict[k] == v}
        self.ctx.narrowed_types = merged
