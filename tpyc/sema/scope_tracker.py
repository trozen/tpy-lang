"""
TurboPython Scope Tracker

Manages scope depth tracking for loop analysis and detects when a pointer
to a local variable might outlive its storage (scope escape detection).
"""

from __future__ import annotations
from contextlib import contextmanager
from typing import TYPE_CHECKING, Iterator

from ..typesys import TpyType
from ..parse import TpyCoerce, TpyName, TpyFieldAccess, TpySubscript, TpyExpr
from ..namespace import Namespace
from .diagnostics import Scope

if TYPE_CHECKING:
    from .context import SemanticContext
    from .compatibility import TypeCompatibility


class ScopeTracker:
    """Scope depth tracking, loop scope management, and escape detection."""

    def __init__(self, ctx: SemanticContext, compat: TypeCompatibility):
        self.ctx = ctx
        self.compat = compat

    # --- Loop scope management ---

    @contextmanager
    def loop_scope(self) -> Iterator[Scope]:
        """Create an inner scope for a loop body and bump loop_depth."""
        inner_scope = Scope(self.ctx.current_scope)
        old_scope = self.ctx.current_scope
        self.ctx.current_scope = inner_scope
        self.ctx.loop_depth += 1
        try:
            yield inner_scope
        finally:
            self.ctx.loop_depth -= 1
            self.ctx.current_scope = old_scope

    @contextmanager
    def loop_var(self, scope: Scope, name: str, var_type: TpyType,
                 depth: int, is_foreach: bool = False) -> Iterator[None]:
        """Bind a loop variable in scope/namespace and track its depth."""
        scope.define(name, var_type)
        old_depth = self.ctx.var_scope_depth.get(name)
        self.ctx.var_scope_depth[name] = depth
        old_ns = self.ctx.current_ns
        if self.ctx.current_ns:
            inner_ns = Namespace(parent=self.ctx.current_ns)
            inner_ns.bind_variable(name, var_type)
            self.ctx.current_ns = inner_ns
        if is_foreach:
            self.ctx.loop_vars.add(name)
        was_assigned = name in self.ctx.definitely_assigned
        self.ctx.definitely_assigned.add(name)
        try:
            yield
        finally:
            if is_foreach:
                self.ctx.loop_vars.discard(name)
            if not was_assigned:
                self.ctx.definitely_assigned.discard(name)
            self.ctx.current_ns = old_ns
            if old_depth is not None:
                self.ctx.var_scope_depth[name] = old_depth
            else:
                self.ctx.var_scope_depth.pop(name, None)

    # --- Escape detection ---

    def get_expr_scope_depth(self, expr: TpyExpr) -> int:
        """Get the storage scope depth for an expression's root variable."""
        if isinstance(expr, TpyCoerce):
            return self.get_expr_scope_depth(expr.expr)
        if isinstance(expr, TpyName):
            return self.ctx.var_scope_depth.get(expr.name, 0)
        if isinstance(expr, TpyFieldAccess):
            return self.get_expr_scope_depth(expr.obj)
        if isinstance(expr, TpySubscript):
            return self.get_expr_scope_depth(expr.obj)
        # Calls, literals etc. — fresh storage, no escape
        return 0

    def is_scope_escape(self, target_name: str, source_expr: TpyExpr) -> bool:
        """Check if source expression references storage that may not outlive target."""
        if self.compat.is_copy_call(source_expr):
            return False
        if not self.compat.is_lvalue(source_expr):
            return False
        target_depth = self.ctx.var_scope_depth.get(target_name, 0)
        source_depth = self.get_expr_scope_depth(source_expr)
        return source_depth > target_depth

    def check_escape(self, target_name: str, source_expr: TpyExpr,
                     node: object) -> None:
        """Error if source may outlive target's storage."""
        if self.is_scope_escape(target_name, source_expr):
            source_name = self._get_source_name(source_expr)
            raise self.ctx.error(
                f"reference to '{source_name}' may outlive its storage; "
                f"use copy({source_name}) for a safe copy",
                node
            )

    def _get_source_name(self, expr: TpyExpr) -> str:
        """Extract the root variable name from an expression for error messages."""
        if isinstance(expr, TpyCoerce):
            return self._get_source_name(expr.expr)
        if isinstance(expr, TpyName):
            return expr.name
        if isinstance(expr, TpyFieldAccess):
            return self._get_source_name(expr.obj)
        if isinstance(expr, TpySubscript):
            return self._get_source_name(expr.obj)
        return "?"
