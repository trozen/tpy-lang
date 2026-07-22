"""
TurboPython Scope Tracker

Manages scope depth tracking for loop analysis and detects when a pointer
to a local variable might outlive its storage (scope escape detection).
"""

from __future__ import annotations
from contextlib import contextmanager
from typing import TYPE_CHECKING, Iterator

from ..typesys import TpyType, OwnType
from ..parse import TpyCoerce, TpyName, TpyFieldAccess, TpySubscript, TpyExpr, TpyStmt, TpyFunction
from ..namespace import Namespace
from ..diagnostics import Scope

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
        inner_scope = Scope(self.ctx.func.current_scope)
        old_scope = self.ctx.func.current_scope
        self.ctx.func.current_scope = inner_scope
        self.ctx.func.loop_depth += 1
        try:
            yield inner_scope
        finally:
            self.ctx.func.loop_depth -= 1
            self.ctx.func.current_scope = old_scope

    @contextmanager
    def comprehension_scope(self) -> Iterator[Scope]:
        """Create an inner scope for a comprehension (no loop_depth bump)."""
        inner_scope = Scope(self.ctx.func.current_scope)
        old_scope = self.ctx.func.current_scope
        self.ctx.func.current_scope = inner_scope
        self.ctx.in_comprehension += 1
        try:
            yield inner_scope
        finally:
            self.ctx.in_comprehension -= 1
            self.ctx.func.current_scope = old_scope

    @contextmanager
    def lambda_scope(self) -> Iterator[Scope]:
        """Create an inner scope for a lambda body."""
        inner_scope = Scope(self.ctx.func.current_scope)
        old_scope = self.ctx.func.current_scope
        old_ns = self.ctx.func.current_ns
        old_assigned = self.ctx.func.definitely_assigned.copy()
        self.ctx.func.current_scope = inner_scope
        if self.ctx.func.current_ns:
            inner_ns = Namespace(parent=self.ctx.func.current_ns)
            self.ctx.func.current_ns = inner_ns
        try:
            yield inner_scope
        finally:
            self.ctx.func.definitely_assigned = old_assigned
            self.ctx.func.current_scope = old_scope
            self.ctx.func.current_ns = old_ns

    @contextmanager
    def nested_def_scope(self, func_node: TpyFunction) -> Iterator[Scope]:
        """Create an isolated scope for a nested function definition.

        Saves and restores all per-function state so the nested def analysis
        doesn't interfere with the enclosing function.
        """
        # save_function_state deep-copies the whole tracking state, so the
        # restore below would install CLONES of the namespace/scope -- and
        # every binding made after the nested def would land in the clone
        # while consumers holding the original object (e.g.
        # _collect_generator_locals' local_ns) never see it. The nested
        # analysis only ever binds into the child scope/ns created here, so
        # re-attaching the ORIGINAL objects after the restore is safe and
        # keeps their identity stable across the def statement.
        outer_scope = self.ctx.func.current_scope
        outer_ns = self.ctx.func.current_ns
        saved = self.ctx.save_function_state()
        inner_scope = Scope(outer_scope)
        inner_ns = Namespace(parent=outer_ns) if outer_ns else None

        self.ctx.reset_function_tracking()
        self.ctx.func.current_scope = inner_scope
        self.ctx.func.current_ns = inner_ns
        self.ctx.func.current_function = func_node
        self.ctx.func.in_nested_def = True
        self.ctx.func.nested_def_name = func_node.name
        try:
            yield inner_scope
        finally:
            self.ctx.restore_function_state(saved)
            self.ctx.func.current_scope = outer_scope
            self.ctx.func.current_ns = outer_ns

    @contextmanager
    def loop_var(self, scope: Scope, name: str, var_type: TpyType,
                 depth: int, is_foreach: bool = False) -> Iterator[None]:
        """Bind a loop variable in scope/namespace and track its depth."""
        scope.define(name, var_type)
        old_depth = self.ctx.func.var_scope_depth.get(name)
        self.ctx.func.var_scope_depth[name] = depth
        old_ns = self.ctx.func.current_ns
        if self.ctx.func.current_ns:
            inner_ns = Namespace(parent=self.ctx.func.current_ns)
            inner_ns.bind_variable(name, var_type)
            self.ctx.func.current_ns = inner_ns
        if is_foreach:
            self.ctx.func.loop_vars.add(name)
        was_assigned = name in self.ctx.func.definitely_assigned
        self.ctx.func.definitely_assigned.add(name)
        try:
            yield
        finally:
            if is_foreach:
                self.ctx.func.loop_vars.discard(name)
            if not was_assigned:
                self.ctx.func.definitely_assigned.discard(name)
            self.ctx.func.current_ns = old_ns
            if old_depth is not None:
                self.ctx.func.var_scope_depth[name] = old_depth
            else:
                self.ctx.func.var_scope_depth.pop(name, None)

    # --- Escape detection ---

    def get_expr_scope_depth(self, expr: TpyExpr) -> int:
        """Get the storage scope depth for an expression's root variable."""
        if isinstance(expr, TpyCoerce):
            return self.get_expr_scope_depth(expr.expr)
        if isinstance(expr, TpyName):
            return self.ctx.func.var_scope_depth.get(expr.name, 0)
        if isinstance(expr, TpyFieldAccess):
            return self.get_expr_scope_depth(expr.obj)
        if isinstance(expr, TpySubscript):
            return self.get_expr_scope_depth(expr.obj)
        # Calls, literals etc. -- fresh storage, no escape
        return 0

    def is_scope_escape(self, target_name: str, source_expr: TpyExpr) -> bool:
        """Check if source expression references storage that may not outlive target."""
        if self.compat.is_copy_call(source_expr):
            return False
        if not self.compat.is_lvalue(source_expr):
            return False
        target_depth = self.ctx.func.var_scope_depth.get(target_name, 0)
        source_depth = self.get_expr_scope_depth(source_expr)
        return source_depth > target_depth

    def check_escape(self, target_name: str, source_expr: TpyExpr,
                     node: TpyExpr | TpyStmt | None) -> None:
        """Check if source may outlive target's storage.

        Hoisting is only safe when the source variable owns its storage
        (rvalue-initialized). For-each variables and lvalue-initialized
        variables keep the hard error -- they alias other storage that
        hoisting can't fix.
        """
        if self.is_scope_escape(target_name, source_expr):
            source_name = self._get_source_name(source_expr)
            can_hoist = (source_name not in self.ctx.func.loop_vars
                         and source_name in self.ctx.func.rvalue_vars)
            if not can_hoist:
                raise self.ctx.error(
                    f"reference to '{source_name}' may outlive its storage; "
                    f"use copy({source_name}) for a safe copy",
                    node
                )
            self.ctx.warning(
                f"'{source_name}' is declared in a loop body; "
                f"storage hoisted to function scope",
                node
            )
            self.ctx.func.hoisted_vars.add(source_name)
            # Hoisted vars become pointer-locals -- strip Own[T] wrapper
            # since they can no longer own their storage.
            if self.ctx.func.current_scope:
                scope_type = self.ctx.func.current_scope.lookup(source_name)
                if isinstance(scope_type, OwnType):
                    self.ctx.func.current_scope.define(source_name, scope_type.wrapped)

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
