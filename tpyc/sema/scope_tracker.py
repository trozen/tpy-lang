"""
TurboPython Scope Tracker

Manages scope depth tracking for loop analysis and detects when a pointer
to a local variable might outlive its storage (scope escape detection).
"""

from __future__ import annotations
from contextlib import contextmanager
from typing import TYPE_CHECKING, Iterator, NamedTuple

from ..typesys import (
    TpyType, OwnType, held_whole_borrow_sources, recorded_return_borrow_sources)
from ..parse import (
    TpyAwait, TpyCoerce, TpyName, TpyFieldAccess, TpySubscript, TpyIfExpr,
    TpyVarargPack,
    TpyStarUnpack, TpyExpr, TpyStmt, TpyFunction, is_property_getter_read,
)
from ..namespace import Namespace
from ..diagnostics import Scope
from ..value_category import call_returns_cpp_ref, property_access_returns_cpp_ref
from .context import (
    ITER_BORROWER, CallOperands, call_borrow_operands, call_lend_sources)
from .type_ops import signature_may_return_borrow

if TYPE_CHECKING:
    from .context import SemanticContext
    from .compatibility import TypeCompatibility


class LendRoot(NamedTuple):
    """One name a source expression lends storage from."""
    name: str
    # Reached through a spelled call, not by path alone.
    through_call: bool
    # Reached through a callee whose body is not analyzed yet, so whether it
    # lends this name at all is an assumption.
    assumed: bool = False
    # Held only as a reference to the whole object: nothing iterates it or
    # points into it, so growing it invalidates nothing (a genexpr capture).
    # It relaxes invalidation only -- the hold still dangles if the root
    # dies, so escape checks keep it.
    held_whole: bool = False


class DeferredEscape(NamedTuple):
    """An escape whose verdict waits for a callee's borrow fact."""
    target_name: str
    source_expr: TpyExpr
    root_name: str
    # Composed at the bind: it reads the binding function's scope.
    remedy: str
    node: TpyExpr | TpyStmt | None


def lend_roots(ctx: SemanticContext, expr: TpyExpr, through_call: bool = False,
               assumed: bool = False, held_whole: bool = False) -> list[LendRoot]:
    """The named storage `expr` hands out a reference INTO, in source
    order; empty when it builds what it yields.

    A name, a field or an element lends its root. A call lends whatever
    its callee's `return_borrows_from` names -- the receiver, an argument,
    several -- so the question is answered by the same fact the borrow
    tracker files the binding's loans from, never by how the call is
    spelled: a getter, a method, a free function and an operator dunder
    that hand back a reference into `b` all point the binding at `b`.
    """
    if isinstance(expr, TpyCoerce):
        return lend_roots(ctx, expr.expr, through_call, assumed, held_whole)
    if isinstance(expr, TpyName):
        return [LendRoot(expr.name, through_call, assumed, held_whole)]
    if isinstance(expr, (TpyFieldAccess, TpySubscript)):
        return lend_roots(ctx, expr.obj, through_call, assumed, held_whole)
    if isinstance(expr, TpyIfExpr):
        # A mixed arm pair is a prvalue: the binding owns a copy of the
        # chosen arm and points at neither.
        arms = [lend_roots(ctx, expr.then_expr, through_call, assumed, held_whole),
                lend_roots(ctx, expr.else_expr, through_call, assumed, held_whole)]
        return arms[0] + arms[1] if all(arms) else []
    if isinstance(expr, TpyAwait):
        # A borrow-returning await aliases what the awaited call borrows.
        return (lend_roots(ctx, expr.value, through_call, assumed, held_whole)
                if expr.await_result_is_borrow else [])
    if isinstance(expr, TpyVarargPack):
        return [root for arg in expr.args for root in lend_roots(
            ctx, arg.expr if isinstance(arg, TpyStarUnpack) else arg,
            through_call, assumed, held_whole)]
    operands = call_borrow_operands(expr)
    if operands is None:
        # Literals, comprehensions etc. -- fresh storage.
        return []
    # A `@property` read is a path hop like the field it wraps.
    through_call = through_call or not is_property_getter_read(expr)
    sources, facts_pending = _call_result_sources(ctx, expr, operands)
    roots: list[LendRoot] = []
    for source, held in sources:
        roots.extend(lend_roots(ctx, source, through_call,
                                assumed or facts_pending, held_whole or held))
    return roots


def _call_result_sources(ctx: SemanticContext, expr: TpyExpr,
                         operands: CallOperands
                         ) -> tuple[list[tuple[TpyExpr, bool]], bool]:
    """The operands of one call whose storage its result may point into,
    each with whether the result only holds it whole, and whether that
    answer is an assumption about a pending callee."""
    fi, obj = operands.fi, operands.obj
    if is_property_getter_read(expr):
        # The read's own convention is decided per INSTANTIATION (a bare
        # type-param return) and per storage-ref shape, which the body
        # fact cannot see.
        return ([(obj, False)] if property_access_returns_cpp_ref(ctx, expr)
                else []), False
    if not signature_may_return_borrow(fi):
        return [], False
    # The ROOT carries the fact: a call site's resolved fi can be a
    # specialization synthesized before the callee's body facts landed.
    if fi.root.return_borrows_from is None:
        pending = ctx.pending_borrow_fact_fis
        if fi in pending or fi.root in pending:
            # Body not analyzed yet (a forward reference): assume every
            # operand, as the borrow registration does.
            return [(src.expr, False)
                    for src in call_lend_sources(operands,
                                                 expr_type=None)], True
        # A body-less stub records no fact; its declared convention is
        # the answer, and a reference it returns is into its receiver.
        return ([(obj, False)] if obj is not None
                and call_returns_cpp_ref(ctx, fi) else []), False
    return [(src.expr, src.held_whole) for src in call_lend_sources(
        operands, recorded_return_borrow_sources(fi),
        held_whole_borrow_sources(fi), expr_type=None)], False


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
        """Create an inner scope for a comprehension (no loop_depth bump).

        The iterator loan the comprehension files expires here, as a `for`'s
        does in `loop_scope`. Unlike a loop, nothing restores an ENCLOSING
        loop's loan afterwards (no exit-facts merge follows an expression),
        so the loans held on entry are put back rather than dropped.
        """
        enclosing_iter_loans = self.ctx.func.borrow_tracker.loans_held_by(ITER_BORROWER)
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
            # Re-read: a trial scope or a nested def inside the comprehension
            # restores the function state as a deep copy.
            bt = self.ctx.func.borrow_tracker
            bt.remove_borrower(ITER_BORROWER)
            bt.reinstate(ITER_BORROWER, enclosing_iter_loans)

    @contextmanager
    def lambda_scope(self) -> Iterator[Scope]:
        """Create an inner scope for a lambda body."""
        inner_scope = Scope(self.ctx.func.current_scope)
        old_scope = self.ctx.func.current_scope
        old_ns = self.ctx.func.current_ns
        old_assigned = self.ctx.func.definitely_assigned.copy()
        # Like a nested def, a lambda may run any number of times, so its
        # body is not the consuming method's own.
        consuming = self.ctx.in_consuming_method
        self.ctx.in_consuming_method = False
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
            self.ctx.in_consuming_method = consuming

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
        # A consuming method owns its receiver for ITS body only: a nested
        # def may run any number of times, so nothing in it moves `self`.
        consuming = self.ctx.in_consuming_method
        self.ctx.in_consuming_method = False
        body_root = self.ctx.func.body_root
        inner_scope = Scope(outer_scope)
        inner_ns = Namespace(parent=outer_ns) if outer_ns else None

        self.ctx.reset_function_tracking()
        self.ctx.func.current_scope = inner_scope
        self.ctx.func.current_ns = inner_ns
        self.ctx.func.current_function = func_node
        self.ctx.func.body_root = body_root
        self.ctx.func.in_nested_def = True
        self.ctx.func.nested_def_first_literal = self.ctx.literal_counter
        self.ctx.func.nested_def_name = func_node.name
        try:
            with self.deferred_body():
                yield inner_scope
        finally:
            self.ctx.restore_function_state(saved)
            self.ctx.in_consuming_method = consuming

    @contextmanager
    def loop_var(self, scope: Scope, name: str, var_type: TpyType,
                 depth: int, is_foreach: bool = False) -> Iterator[None]:
        """Bind a loop variable in scope/namespace and track its depth."""
        scope.define(name, var_type)
        saved_decl = self.ctx.local_decl_of(name)
        self.ctx.declare_local(name, depth)
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
            self.ctx.restore_local_decl(name, saved_decl)

    # --- Escape detection ---

    def _storage_depth(self, name: str) -> int:
        """The scope depth `name` was declared at."""
        return self.ctx.func.var_scope_depth.get(name, 0)

    def get_expr_scope_depth(self, expr: TpyExpr) -> int:
        """The deepest storage scope `expr` lends from; 0 for fresh storage."""
        return max((self._storage_depth(root.name)
                    for root in lend_roots(self.ctx, expr)), default=0)

    def _escaping_roots(self, target_name: str,
                        source_expr: TpyExpr) -> list[LendRoot]:
        """The roots `source_expr` lends from that may not outlive the target,
        one per name."""
        if self.compat.is_copy_call(source_expr):
            return []
        target_depth = self._storage_depth(target_name)
        by_name: dict[str, LendRoot] = {}
        for root in lend_roots(self.ctx, source_expr):
            if self._storage_depth(root.name) <= target_depth:
                continue
            # A name lent for certain anywhere in the source is certain.
            if root.name not in by_name or by_name[root.name].assumed:
                by_name[root.name] = root
        return list(by_name.values())

    def check_escape(self, target_name: str, source_expr: TpyExpr,
                     node: TpyExpr | TpyStmt | None) -> None:
        """Diagnose binding storage that may not outlive its new name.

        Hoisting the source's storage to function scope keeps the target
        from dangling, but both names then share ONE slot, so the target
        observes whatever the source is rebound to later -- a divergence
        from CPython, which the warning states and `copy()` avoids.
        For-each and lvalue-initialized sources cannot be hoisted at all
        (they alias other storage), so those stay a hard error -- when the
        source names them by path; behind a call they are not judged.
        """
        # Every root is judged before any is hoisted: a source lending from
        # two scopes (a ternary, a call borrowing two arguments) must not
        # leave one root hoisted behind the other's error.
        escaping = self._escaping_roots(target_name, source_expr)
        for root in escaping:
            if not root.through_call and not self._can_hoist(root.name):
                raise self._escape_error(root.name, self._escape_remedy(root), node)
        for root in escaping:
            if not self._can_hoist(root.name):
                # An alias or a for-each var behind a CALL. Its own depth says
                # nothing about the storage it refers to -- as often as not
                # the caller's -- and nothing here can tell a real dangle from
                # `for v in d.values(): best = v.child()`, so it is left
                # undiagnosed rather than rejected:
                # BUGS.md#call-source-escape-through-alias-unchecked.
                continue
            remedy = self._escape_remedy(root)
            self._hoist(target_name, root.name, warned=not root.assumed)
            if root.assumed:
                # The storage decision cannot wait -- it shapes the rest of
                # this body -- so the root is hoisted on the assumption. The
                # DIAGNOSTIC can: it is settled once the callee's fact lands,
                # which keeps it independent of definition order.
                self.ctx.deferred_escapes.append(DeferredEscape(
                    target_name, source_expr, root.name, remedy, node))
            else:
                self._warn_shared_slot(target_name, root.name, remedy, node)

    def settle_deferred_escapes(self) -> None:
        """Answer the escapes that waited for a callee's borrow fact.

        Runs after every body in the module: an assumed root the callee turns
        out not to lend needed no diagnostic (its precautionary hoist stays,
        which only moves when the local is destroyed)."""
        deferred = self.ctx.deferred_escapes
        self.ctx.deferred_escapes = []
        for entry in deferred:
            if any(root.name == entry.root_name
                   for root in lend_roots(self.ctx, entry.source_expr)):
                self._warn_shared_slot(entry.target_name, entry.root_name,
                                       entry.remedy, entry.node)

    def _can_hoist(self, source_name: str) -> bool:
        return (source_name not in self.ctx.func.loop_vars
                and source_name in self.ctx.func.rvalue_vars)

    def _escape_error(self, source_name: str, remedy: str,
                      node: TpyExpr | TpyStmt | None) -> Exception:
        return self.ctx.error(
            f"reference to '{source_name}' may outlive its storage; {remedy}",
            node)

    def _escape_remedy(self, root: LendRoot) -> str:
        # copy() is not available for a @nocopy source -- naming it there
        # sends the author into a second, unrelated rejection.
        source_type = (self.ctx.func.current_scope.lookup(root.name)
                       if self.ctx.func.current_scope else None)
        if source_type is not None and self.ctx.is_type_nocopy(source_type):
            return f"'{root.name}' cannot be copied"
        if root.through_call:
            # copy() of the ROOT is the wrong remedy behind a call: the call
            # would then hand back a reference into the dying copy.
            return "use copy() on the assigned value for an independent value"
        return f"use copy({root.name}) for an independent value"

    def _warn_shared_slot(self, target_name: str, source_name: str,
                          remedy: str,
                          node: TpyExpr | TpyStmt | None) -> None:
        # States the CONSEQUENCE, not the mechanism: the old text named the
        # hoist, which told the reader nothing about the value they would
        # get. Deliberately avoids "give it longer-lived storage" framing --
        # hoisting the declaration above the loop is the neighbouring
        # spelling that diverges with no diagnostic at all.
        self.ctx.warning(
            f"'{target_name}' will not keep the object it was given -- "
            f"'{source_name}' is rebound on each iteration and both names "
            f"share its storage; {remedy}",
            node
        )

    def _hoist(self, target_name: str, source_name: str, *,
               warned: bool) -> None:
        self.ctx.func.hoisted_vars.add(source_name)
        self.ctx.func.escape_hoisted_vars.add(source_name)
        if warned:
            # Only a pair this check DID diagnose: the alias-rebind pass
            # reads the set as "already warned" and stays quiet for it.
            self.ctx.func.escape_warned_aliases.add((target_name, source_name))
        # Hoisted vars become pointer-locals -- strip Own[T] wrapper
        # since they can no longer own their storage.
        if self.ctx.func.current_scope:
            scope_type = self.ctx.func.current_scope.lookup(source_name)
            if isinstance(scope_type, OwnType):
                self.ctx.func.current_scope.define(source_name, scope_type.wrapped)
