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
from .context import ITER_BORROWER

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
    def block_scope(self) -> Iterator[Scope]:
        """Create an inner scope and namespace for a statement's block.

        The bindings made in it do not outlive the block as scope bindings --
        a read after the enclosing statement promotes them from the pending
        table instead, as the function-scoped locals Python makes them. Used
        for a clause that is a block but not a loop body (a loop's `else`),
        where bumping `loop_depth` would misdirect a `break` at the clause's
        own statement instead of the enclosing loop.
        """
        inner_scope = Scope(self.ctx.func.current_scope)
        old_scope = self.ctx.func.current_scope
        old_ns = self.ctx.func.current_ns
        if old_ns:
            inner_scope.namespace = Namespace(parent=old_ns)
            self.ctx.func.current_ns = inner_scope.namespace
        self.ctx.func.current_scope = inner_scope
        try:
            yield inner_scope
        finally:
            self.ctx.func.current_scope = old_scope
            self.ctx.func.current_ns = old_ns

    @contextmanager
    def loop_scope(self, body: list[TpyStmt] | None = None) -> Iterator[Scope]:
        """Create an inner scope for a loop body and bump loop_depth.

        The implicit iterator loan a `for` registers expires here: the
        iterator is a temporary of the statement, so the statement's own
        scope releases it -- carried past the exit it would never expire and
        would warn on every post-loop mutation of the iterable. Keyed by
        holder rather than by the storage the lowering registered, because a
        rebind in the body retargets the loan to another storage key but
        never renames the holder. A shape that registers none (`while`, the
        enum-iterable `for`) drops nothing of its own; an ENCLOSING loop's
        loan is dropped along with it and comes back from the `before`
        snapshot every caller's exit-facts merge restores.
        """
        with self.block_scope() as inner_scope:
            self.ctx.func.loop_depth += 1
            try:
                yield inner_scope
            finally:
                self.ctx.func.loop_depth -= 1
                self.ctx.func.borrow_tracker.remove_borrower(ITER_BORROWER)

    @contextmanager
    def deferred_body(self) -> Iterator[None]:
        """Analyze a body whose statements run inside their own region.

        A comprehension, generator expression, lambda or nested def opens a
        region of its own, so a temporary built in it is placed there and not
        at the enclosing statement -- even when the whole expression sits in a
        conditionally evaluated operand. Leaving the enclosing
        `cond_operand_depth` in place would make
        `warn_cond_operand_eager_arg` report an early build that does not
        happen, and name a remedy (bind the argument to a local before the
        expression) that a per-iteration value cannot take.
        """
        saved = self.ctx.cond_operand_depth
        self.ctx.cond_operand_depth = 0
        try:
            yield
        finally:
            self.ctx.cond_operand_depth = saved

    @contextmanager
    def comprehension_scope(self) -> Iterator[Scope]:
        """Create an inner scope for a comprehension (no loop_depth bump)."""
        inner_scope = Scope(self.ctx.func.current_scope)
        old_scope = self.ctx.func.current_scope
        old_ns = self.ctx.func.current_ns
        if old_ns:
            # Nothing harvests this region; it exists so `loop_var` binds
            # the comprehension variable into an enclosing region in every
            # construct, and the variable does not leak past the expression.
            inner_scope.namespace = Namespace(parent=old_ns)
            self.ctx.func.current_ns = inner_scope.namespace
        self.ctx.func.current_scope = inner_scope
        self.ctx.in_comprehension += 1
        try:
            with self.deferred_body():
                yield inner_scope
        finally:
            self.ctx.in_comprehension -= 1
            self.ctx.func.current_scope = old_scope
            self.ctx.func.current_ns = old_ns

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
            with self.deferred_body():
                yield inner_scope
        finally:
            self.ctx.func.definitely_assigned = old_assigned
            self.ctx.func.current_scope = old_scope
            self.ctx.func.current_ns = old_ns

    @contextmanager
    def nested_def_scope(self, func_node: TpyFunction) -> Iterator[Scope]:
        """Create an isolated scope for a nested function definition.

        Saves and restores all per-function state so the nested def analysis
        doesn't interfere with the enclosing function. The nested analysis
        only ever binds into the child scope/namespace created here; the
        enclosing ones come back live from the restore, along with the
        enclosing function node (see `save_function_state`).
        """
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
            with self.deferred_body():
                yield inner_scope
        finally:
            self.ctx.restore_function_state(saved)

    @contextmanager
    def loop_var(self, scope: Scope, name: str, var_type: TpyType,
                 depth: int, is_foreach: bool = False) -> Iterator[None]:
        """Bind a loop variable in scope/namespace and track its depth."""
        scope.define(name, var_type)
        old_depth = self.ctx.func.var_scope_depth.get(name)
        self.ctx.func.var_scope_depth[name] = depth
        # The enclosing loop / comprehension body already opened the
        # namespace region the variable belongs to.
        if self.ctx.func.current_ns:
            self.ctx.func.current_ns.bind_variable(name, var_type)
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
        """Diagnose binding storage that may not outlive its new name.

        Hoisting the source's storage to function scope keeps the target
        from dangling, but both names then share ONE slot, so the target
        observes whatever the source is rebound to later -- a divergence
        from CPython, which the warning states and `copy()` avoids.
        For-each and lvalue-initialized sources cannot be hoisted at all
        (they alias other storage), so those stay a hard error.
        """
        if not self.is_scope_escape(target_name, source_expr):
            return
        source_name = self._get_source_name(source_expr)
        # copy() is not available for a @nocopy source -- naming it there
        # sends the author into a second, unrelated rejection.
        source_type = (self.ctx.func.current_scope.lookup(source_name)
                       if self.ctx.func.current_scope else None)
        copyable = not (source_type is not None
                        and self.ctx.is_type_nocopy(source_type))
        fix = (f"use copy({source_name}) for an independent value"
               if copyable else f"'{source_name}' cannot be copied")
        can_hoist = (source_name not in self.ctx.func.loop_vars
                     and source_name in self.ctx.func.rvalue_vars)
        if not can_hoist:
            raise self.ctx.error(
                f"reference to '{source_name}' may outlive its storage; {fix}",
                node
            )
        # States the CONSEQUENCE, not the mechanism: the old text named the
        # hoist, which told the reader nothing about the value they would
        # get. Deliberately avoids "give it longer-lived storage" framing --
        # hoisting the declaration above the loop is the neighbouring
        # spelling that diverges with no diagnostic at all.
        self.ctx.warning(
            f"'{target_name}' will not keep the object it was given -- "
            f"'{source_name}' is rebound on each iteration and both names "
            f"share its storage; {fix}",
            node
        )
        self.ctx.func.hoisted_vars.add(source_name)
        self.ctx.func.escape_warned_aliases.add((target_name, source_name))
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
