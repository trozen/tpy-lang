"""Whether running a body may reach a Ctrl-C check point.

One fact per analyzed body, `FunctionInfo.may_interrupt`. A check point
(`print`, `time.sleep`, a file read, ...) raises a pending Ctrl-C as
`KeyboardInterrupt`; inside a body C++ runs under `noexcept` (`__del__`,
`__move__`, the `std::hash` wrapper, an abandoned frame's destructor) that
would terminate the process, so codegen opens a `::tpy::DeferSignals` scope
there unless the body is INERT.

INERT is a positive allowlist over the body alone -- no call graph, no
propagation: builtin operations on inert types, and calls to bodyless
bindings that are not marked `checks_signals=True`. Anything the walk does
not recognize makes the body not inert, so the answer errs toward the
scope (a deferral costs a counter bump; a missing one terminates).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..parse import (TpyAssert, TpyAssign, TpyAugAssign, TpyBreak, TpyContinue,
                     TpyDelVar, TpyExprStmt, TpyForEach, TpyIf, TpyNestedDef,
                     TpyPassStmt, TpyRaise, TpyReturn, TpyStmt, TpyTry,
                     TpyTupleUnpack, TpyVarDecl, TpyWhile, TpyYield)
from ..parse.nodes import (
    TpyArrayLiteral, TpyBinOp, TpyBoolLiteral, TpyBytesLiteral, TpyCall,
    TpyChainedCompare, TpyCoerce, TpyDictLiteral, TpyExpr, TpyFieldAccess,
    TpyFloatLiteral, TpyFString, TpyIfExpr, TpyIntLiteral, TpyListRepeat,
    TpyMethodCall, TpyName, TpyNamedExpr, TpyNoneLiteral, TpySetLiteral,
    TpySlice, TpyStrLiteral, TpySubscript, TpyTupleLiteral, TpyUnaryOp,
    TpyVarargPack, VarLinkage, is_super_del_call, walk_body_stmts,
    walk_expr_tree)
from ..typesys import is_bodyless_binding

if TYPE_CHECKING:
    from ..parse import TpyFunction
    from ..typesys import FunctionInfo, TpyType
    from .context import SemanticContext


def is_inert_type(t: 'TpyType | None') -> bool:
    """No builtin operation on a value of `t` can run user code
    (`TpyType.value_ops_run_user_code`); an untyped expression is not."""
    return t is not None and not t.value_ops_run_user_code()


class _NotInert(Exception):
    pass


class _InertWalk:
    def __init__(self, ctx: 'SemanticContext', func: 'TpyFunction') -> None:
        self.ctx = ctx
        self.param_types = {name: t for name, t in func.params}

    def require(self, cond: bool) -> None:
        if not cond:
            raise _NotInert

    def typed(self, e: TpyExpr) -> None:
        self.require(is_inert_type(self.ctx.get_expr_type(e)))

    # -- statements ---------------------------------------------------------

    def stmt(self, s: TpyStmt) -> None:
        # A `yield` only suspends the frame (its value is checked like any
        # expression), so a generator whose pending `finally` is inert keeps
        # a scope-free frame destructor.
        if isinstance(s, (TpyExprStmt, TpyIf, TpyWhile, TpyReturn, TpyAssert,
                          TpyTry, TpyPassStmt, TpyBreak, TpyContinue,
                          TpyYield)):
            return
        if isinstance(s, TpyVarDecl):
            declared = s.type if s.type is not None else (
                self.ctx.get_expr_type(s.init) if s.init is not None else None)
            self.require(s.linkage is VarLinkage.DEFAULT
                         and is_inert_type(declared))  # type: ignore[arg-type]
        elif isinstance(s, TpyAssign):
            self.target(s.target)
        elif isinstance(s, TpyAugAssign):
            self.target(s.target)
        elif isinstance(s, TpyTupleUnpack):
            self.require(bool(s.target_types)
                         and all(is_inert_type(t) for t in s.target_types))
        elif isinstance(s, TpyDelVar):
            # A local was bound by a checked statement; a parameter was not.
            self.require(all(is_inert_type(self.param_types[n])
                             for n in s.names if n in self.param_types))
        elif isinstance(s, TpyRaise):
            self.require(not s.raise_via_virtual and s.deref_depth == 0)
            if s.resolved_ctor_init is not None:
                self.require(self.unmarked_bodyless(s.resolved_ctor_init))
        elif isinstance(s, TpyForEach):
            # Iterating an inert-typed source (a builtin container, str,
            # bytes, range) is runtime code; a user `__iter__` is not.
            self.require(not s.is_async and s.consuming_iter_fi is None
                         and s.enum_iterable is None
                         and is_inert_type(self.ctx.get_expr_type(s.iterable))
                         and is_inert_type(s.elem_type))
        else:
            raise _NotInert

    def target(self, t: TpyExpr) -> None:
        """An assignment target: a local, or a `self.<field>` path."""
        if isinstance(t, TpyName):
            return
        self.require(isinstance(t, TpyFieldAccess))
        root = self.field_chain_root(t)
        self.require(isinstance(root, TpyName) and root.name == "self")

    # -- expressions --------------------------------------------------------

    def field_chain_root(self, e: TpyFieldAccess) -> TpyExpr:
        """The root under a chain of plain field reads (no property,
        `__getattr__` family or `__deref__` hop)."""
        cur: TpyExpr = e
        while isinstance(cur, TpyFieldAccess):
            self.require(cur.hidden_call is None and cur.deref_depth == 0
                         and cur.resolved_property_getter is None
                         and not cur.property_setter)
            cur = cur.obj
        return cur

    def walk(self, e: TpyExpr) -> None:
        walk_expr_tree(e, self.visit)

    def visit(self, e: TpyExpr) -> bool:
        """Judge one node; True descends into its `children()`. A node judged
        as a whole -- a field chain, a call (whose callee name is a child),
        a subscript with its slice -- walks the parts it admits itself and
        answers False."""
        if isinstance(e, (TpyIntLiteral, TpyFloatLiteral, TpyStrLiteral,
                          TpyBytesLiteral, TpyBoolLiteral, TpyNoneLiteral)):
            return False
        if isinstance(e, TpyCoerce):
            self.require(is_inert_type(e.actual_type)
                         and is_inert_type(e.expected_type))
            return True
        if isinstance(e, TpyName):
            self.require(not e.is_function_ref and e.factory_expansion is None)
            self.typed(e)
            return False
        if isinstance(e, TpyFieldAccess):
            root = self.field_chain_root(e)
            # The chain only reads through its intermediate hops (`self` is a
            # record); what it yields must be inert.
            if not isinstance(root, TpyName):
                self.walk(root)
            self.typed(e)
            return False
        if isinstance(e, TpyCall):
            self.call(e)
            return False
        if isinstance(e, TpyMethodCall):
            self.method_call(e)
            return False
        if isinstance(e, TpySubscript):
            self.require(e.getitem_function_info is None
                         and e.typed_dict_field is None
                         and e.enum_from_name is None)
            if e.slice_function_info is not None:
                self.require(self.unmarked_bodyless(e.slice_function_info))
            self.walk(e.obj)
            if isinstance(e.index, TpySlice):
                for part in e.index.children():
                    self.walk(part)
            else:
                self.walk(e.index)
            self.typed(e)
            return False
        if isinstance(e, TpyChainedCompare):
            if e.pairs is None:
                return True
            for part in e.pairs:
                self.walk(part)
            return False
        if isinstance(e, TpyBinOp):
            # Operand types inert leaves no user dunder for the operator or a
            # `__contains__` to resolve to.
            self.typed(e)
            return True
        if isinstance(e, TpyFString):
            return True
        if isinstance(e, (TpyUnaryOp, TpyIfExpr, TpyNamedExpr, TpyArrayLiteral,
                          TpyTupleLiteral, TpySetLiteral, TpyDictLiteral,
                          TpyListRepeat)):
            self.typed(e)
            return True
        if isinstance(e, TpyVarargPack):
            self.require(is_inert_type(e.element_type))
            for child in e.args:
                self.walk(child)
            return False
        raise _NotInert

    def call_args(self, args: list[TpyExpr], kwargs: dict[str, TpyExpr]) -> None:
        for a in args:
            self.walk(a)
        for a in kwargs.values():
            self.walk(a)

    def call(self, e: TpyCall) -> None:
        self.require(isinstance(e.func, TpyName)
                     and e.macro_expansion is None and e.dunder_call is None
                     and e.dyn_hasattr_call is None
                     and e.dyn_getattr_default_call is None
                     and e.kwarg_td_call is None
                     and e.double_star_unpack is None
                     and e.subscript_callee is None)
        if e.isinstance_type is not None or e.enum_from_value is not None:
            # A type test / an enum lookup by value: the subject decides.
            self.call_args(e.args, e.kwargs)
            self.typed(e)
            return
        self.require(e.resolved_function_info is not None
                     and self.unmarked_bodyless(e.resolved_function_info))
        self.call_args(e.args, e.kwargs)
        self.typed(e)

    def method_call(self, e: TpyMethodCall) -> None:
        self.require(e.fstr_expansion is None and e.macro_expansion is None
                     and not e.is_callable_field
                     and e.typed_dict_get_field is None
                     and not e.is_nested_constructor
                     and not e.is_nested_enum_constructor
                     and e.deref_depth == 0 and e.super_parent_type is None
                     and e.unbound_self_parent_type is None
                     and e.subscript_callee is None
                     and e.double_star_unpack is None
                     and e.resolved_function_info is not None
                     and self.unmarked_bodyless(e.resolved_function_info))
        # A module function or a static method has no receiver value.
        if not (e.user_module_call or e.builtin_module_call or e.is_static_call):
            self.walk(e.obj)
        self.call_args(e.args, e.kwargs)
        self.typed(e)

    def unmarked_bodyless(self, fi: 'FunctionInfo') -> bool:
        """A binding the compiler sees no body for, which does not declare
        itself a check point. Its parameter types need no check: an
        argument of an inert type reaches no user code through a protocol
        or generic parameter (`hash(x: Hashable)` on an int32), and the
        arguments are checked one by one."""
        root = fi.root
        decl = root.declaration
        if decl is not None and decl.body:
            return False
        if root.checks_signals or fi.checks_signals:
            return False
        if root.special_handling or root.is_callable_value:
            return False
        if is_bodyless_binding(root):
            return True
        # A `native_module` is declaration-only: its `...` methods (the
        # builtin exception initializers) bind C++ without a @native each.
        mod = (self.ctx.registry.get_module(root.originating_module)
               if root.originating_module else None)
        return mod is not None and mod.is_native_module


def binding_may_interrupt(func: 'TpyFunction') -> bool:
    """A bodyless binding's fact at registration: what it declares. A
    bodied function keeps the default until its body is stamped."""
    return func.checks_signals or not is_bodyless_binding(func)


# The bodies codegen runs under noexcept, with how a diagnostic names each.
_CLEANUP_BODIES = {
    "__del__": "a destructor",
    "__move__": "a move",
    "__hash__": "a hash computed by a dict or set",
}
# Clause spellings that catch a KeyboardInterrupt (a subclass of it would not).
_CATCHES_INTERRUPT = frozenset({None, "KeyboardInterrupt", "BaseException"})


def warn_dead_interrupt_handlers(ctx: 'SemanticContext', func: 'TpyFunction',
                                 stmts: 'list[TpyStmt] | None' = None) -> None:
    """A handler for KeyboardInterrupt written inside a cleanup body is dead:
    the Ctrl-C defers past the body instead of raising inside it (CPython's
    handler would run). Lexical, like the escaping-raise check it runs beside
    at registration; a nested def is a body of its own."""
    if func.name not in _CLEANUP_BODIES:
        return
    for s in (func.body if stmts is None else stmts):
        if isinstance(s, TpyNestedDef):
            continue
        if isinstance(s, TpyTry):
            for h in s.handlers:
                if h.exception_type in _CATCHES_INTERRUPT:
                    clause = ("except:" if h.exception_type is None
                              else f"except {h.exception_type}")
                    ctx.warning_from_loc(
                        f"'{clause}' in '{func.name}' never runs: a Ctrl-C "
                        f"inside {_CLEANUP_BODIES[func.name]} is deferred to "
                        f"the next check point after it",
                        h.loc or s.loc)
        for body in s.sub_bodies():
            warn_dead_interrupt_handlers(ctx, func, body)


def stamp_may_interrupt(ctx: 'SemanticContext', func: 'TpyFunction',
                        fi: 'FunctionInfo') -> None:
    """Record `may_interrupt` at the end of a body's Phase-1 analysis: False
    only when the allowlist walk accepts every statement and expression."""
    walk = _InertWalk(ctx, func)
    # `super().__del__()` emits nothing: the parent's destructor runs after
    # this one as a body of its own, with its own fact.
    body = [s for s in func.body if not is_super_del_call(s)]
    try:
        walk_body_stmts(body, walk.walk, walk.stmt)
    except _NotInert:
        fi.may_interrupt = True
        return
    fi.may_interrupt = False
