"""
TurboPython Definite-Assignment Tracker

Tracks which variables are guaranteed to be initialized on all paths
reaching the current point. Prevents use-before-assignment errors that
would be undefined behavior in generated C++.
"""

from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .context import SemanticContext

# (definitely_assigned, init_terminated, rvalue_vars)
FlowState = tuple[frozenset[str], bool, frozenset[str]]


class InitTracker:
    """Definite-assignment and rvalue-vars flow analysis."""

    def __init__(self, ctx: SemanticContext):
        self.ctx = ctx

    def save(self) -> FlowState:
        return (frozenset(self.ctx.definitely_assigned),
                self.ctx.init_terminated,
                frozenset(self.ctx.rvalue_vars))

    def restore(self, state: FlowState) -> None:
        self.ctx.definitely_assigned = set(state[0])
        self.ctx.init_terminated = state[1]
        self.ctx.rvalue_vars = set(state[2])

    def mark_assigned(self, name: str) -> None:
        self.ctx.definitely_assigned.add(name)

    def mark_terminated(self) -> None:
        self.ctx.init_terminated = True

    def merge_branches(self, then_state: FlowState, else_state: FlowState) -> None:
        then_assigned, then_term, then_rvalue = then_state
        else_assigned, else_term, else_rvalue = else_state
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
