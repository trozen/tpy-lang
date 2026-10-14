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
    TpyChainedCompare, TpyCoerce, TpyDictComprehension, TpyDictLiteral,
    TpyExpr, TpyFieldAccess, TpyListComprehension, TpySetComprehension,
    TpyFloatLiteral, TpyFString, TpyIfExpr, TpyIntLiteral, TpyListRepeat,
    TpyMethodCall, TpyName, TpyNamedExpr, TpyNoneLiteral, TpySetLiteral,
    TpySlice, TpyStrLiteral, TpySubscript, TpyTupleLiteral, TpyUnaryOp,
    TpyVarargPack, VarLinkage, is_super_del_call, walk_body_stmts,
    walk_expr_tree)
from ..type_def_registry import (RUNTIME_CONTAINER_CATEGORIES,
                                 RUNTIME_VALUE_CATEGORIES, type_def_of)
from ..typesys import (PendingContainerType, PtrType, TupleType,
                       is_bodyless_binding, is_declaration_only,
                       unwrap_qualifiers)

if TYPE_CHECKING:
    from ..parse import TpyFunction
    from ..typesys import FunctionInfo, TpyType
    from .context import SemanticContext


def is_inert_type(t: 'TpyType | None') -> bool:
    """No builtin operation on a value of `t` can run user code
    (`TpyType.value_ops_run_user_code`); an untyped expression is not."""
    return t is not None and not t.value_ops_run_user_code()


class NotInert(Exception):
    pass


class InertWalk:
    """The allowlist walk over statements and expressions: every node is a
    kind this walk knows, and every value it touches runs no user code
    (`is_inert_type`). A subclass narrows or extends it through the hooks
    -- which callees count (`admitted`), what a call, a node, a store
    target and a binding must also satisfy (`on_call`, `on_node`,
    `target`, `rebind`), whether a statement that suspends or unwinds is
    admitted (`admits_unwinding`), and whether a comprehension or a nested
    `def` is (`admits_nested`) -- so every arm exists once."""

    # A `yield` only suspends the frame (its value is checked like any
    # expression), and a `try` / `raise` with inert parts runs no user code
    # of its own: a generator whose pending `finally` is inert keeps a
    # scope-free frame destructor.
    admits_unwinding = True
    # A comprehension (walked as its clauses' iterations, filters and
    # element) and a nested `def` (whose definition runs nothing; a call
    # of it is a call into a body). Off here: the deferral fact keeps its
    # verdict on both.
    admits_nested = False

    def __init__(self, ctx: 'SemanticContext',
                 func: 'TpyFunction | None') -> None:
        self.ctx = ctx
        self.param_types = ({name: t for name, t in func.params}
                            if func is not None else {})

    def require(self, cond: bool) -> None:
        if not cond:
            raise NotInert

    def typed(self, e: TpyExpr) -> None:
        self.require(is_inert_type(self.ctx.get_expr_type(e)))

    # -- hooks --------------------------------------------------------------

    def admitted(self, fi: 'FunctionInfo') -> bool:
        """A callee the walk accepts: one the compiler sees no body for,
        which does not declare itself a check point. Its parameter types
        need no check: an argument of an inert type reaches no user code
        through a protocol or generic parameter (`hash(x: Hashable)` on an
        int32), and the arguments are checked one by one."""
        root = fi.root
        if root.checks_signals or fi.checks_signals or root.special_handling:
            return False
        return is_declaration_only(fi)

    def on_node(self, node: 'TpyExpr | TpyStmt') -> None:
        """What every statement and expression must also satisfy."""

    def on_call(self, call: 'TpyExpr | TpyStmt') -> None:
        """What an admitted call (or operator) must also satisfy."""

    def rebind(self, name: str, t: 'TpyType | None') -> None:
        """What a whole-name binding or `del` must also satisfy."""

    def target(self, t: TpyExpr) -> None:
        """An assignment target: a local, or a `self.<field>` path."""
        if isinstance(t, TpyName):
            self.rebind(t.name, self.ctx.get_expr_type(t))
            return
        self.require(isinstance(t, TpyFieldAccess))
        root = self.field_chain_root(t)  # type: ignore[arg-type]
        self.require(isinstance(root, TpyName) and root.name == "self")

    # -- statements ---------------------------------------------------------

    def stmt(self, s: TpyStmt) -> None:
        self.on_node(s)
        if isinstance(s, (TpyTry, TpyYield, TpyRaise)):
            self.require(self.admits_unwinding)
        if isinstance(s, (TpyExprStmt, TpyIf, TpyWhile, TpyReturn, TpyAssert,
                          TpyTry, TpyPassStmt, TpyBreak, TpyContinue,
                          TpyYield)):
            return
        if isinstance(s, TpyNestedDef):
            self.require(self.admits_nested)
            return
        if isinstance(s, TpyVarDecl):
            declared = s.type if s.type is not None else (
                self.ctx.get_expr_type(s.init) if s.init is not None else None)
            self.require(s.linkage is VarLinkage.DEFAULT
                         and is_inert_type(declared))  # type: ignore[arg-type]
            # A name the body binds more than once: any of its declarations
            # may replace a previous object.
            if s.name in self.ctx.func.current_reassigned_vars:
                self.rebind(s.name, declared)
        elif isinstance(s, TpyAssign):
            self.target(s.target)
        elif isinstance(s, TpyAugAssign):
            self.target(s.target)
            self.on_call(s)
        elif isinstance(s, TpyTupleUnpack):
            self.require(bool(s.target_types)
                         and all(is_inert_type(t) for t in s.target_types))
            for name, t in zip(s.targets, s.target_types):
                if name is not None:
                    self.rebind(name, t)
        elif isinstance(s, TpyDelVar):
            # The deleted object is destroyed: a parameter's type, else the
            # type the name is bound at (a name bound inside the walked
            # statements was checked there).
            for n in s.names:
                t = self.param_types.get(n)
                if t is None:
                    scope = self.ctx.func.current_scope
                    t = scope.lookup(n) if scope is not None else None
                self.require(t is None or is_inert_type(t))
                self.rebind(n, t)
        elif isinstance(s, TpyRaise):
            self.require(not s.raise_via_virtual and s.deref_depth == 0)
            if s.resolved_ctor_init is not None:
                self.require(self.admitted(s.resolved_ctor_init))
        elif isinstance(s, TpyForEach):
            # Iterating an inert-typed source (a builtin container, str,
            # bytes, range) is runtime code; a user `__iter__` is not. Its
            # variable is rebound to the next element: no object goes.
            self.require(not s.is_async and s.consuming_iter_fi is None
                         and s.enum_iterable is None
                         and is_inert_type(self.ctx.get_expr_type(s.iterable))
                         and is_inert_type(s.elem_type))
        else:
            raise NotInert

    # -- expressions --------------------------------------------------------

    def field_chain_root(self, e: TpyFieldAccess) -> TpyExpr:
        """The root under a chain of plain field reads; a dotted path naming a
        class or enum (`m.Outer`) is a root of its own."""
        cur: TpyExpr = e
        while isinstance(cur, TpyFieldAccess) and cur.names_type is None:
            self.require(self.plain_field_read(cur))
            cur = cur.obj
        return cur

    def plain_field_read(self, e: TpyFieldAccess) -> bool:
        """`e` reads a field its receiver's class declares, or a module
        variable, an enum member or a class constant. A property, a
        `__getattr__` family or `__deref__` hop, or a protocol attribute
        (whose implementer may answer with a property) runs code, and a
        receiver whose class is unknown here is not shown to run none."""
        if (e.hidden_call is not None or e.deref_depth != 0
                or e.resolved_property_getter is not None
                or e.property_setter):
            return False
        if (e.module_var_access is not None or e.enum_member_of is not None
                or e.class_constant_owner is not None):
            return True
        t = self.ctx.get_expr_type(e.obj)
        if t is None:
            return False
        t = unwrap_qualifiers(t)
        if isinstance(t, PtrType):
            t = unwrap_qualifiers(t.inner_pointee)
        record = self.ctx.registry.get_record_for_type(t)
        return record is not None and any(
            f.name == e.field for f in self.ctx.registry.get_all_fields(record))

    def builtin_element_read(self, e: TpySubscript) -> bool:
        """`e` reads an element of a builtin container, a str / bytes, a
        tuple or through a pointer: sema stamped no class `__getitem__` on
        it (`record_getitem`, `getitem_callee`), no TypedDict field or enum
        name lookup, and the receiver's family reads elements in runtime
        code (a protocol's or a type parameter's implementer may run
        anything)."""
        if (e.record_getitem is not None
                or e.getitem_callee is not None
                or e.typed_dict_field is not None
                or e.enum_from_name is not None):
            return False
        t = self.ctx.get_expr_type(e.obj)
        if t is None:
            return False
        t = unwrap_qualifiers(t)
        if isinstance(t, (TupleType, PendingContainerType, PtrType)):
            return True
        td = type_def_of(t)
        return td is not None and (td.category in RUNTIME_CONTAINER_CATEGORIES
                                   or td.category in RUNTIME_VALUE_CATEGORIES)

    def walk(self, e: TpyExpr) -> None:
        walk_expr_tree(e, self.visit)

    def place(self, e: TpyExpr) -> None:
        """A place read through, not used as a value: a plain field chain's
        base and a builtin subscript's container run nothing whatever they
        hold (`self._store[name].value` reads a record without running any
        of its code). Its parts used as values -- a subscript key, which is
        hashed and compared, a call yielding the base -- are walked as
        values."""
        if isinstance(e, (TpyFieldAccess, TpySubscript)):
            self.on_node(e)
            self.read_through(e)
        elif isinstance(e, TpyName):
            self.require(not e.is_function_ref and e.factory_expansion is None)
        else:
            self.walk(e)

    def read_through(self, e: 'TpyFieldAccess | TpySubscript') -> None:
        """The parts of a field or element read: the place under it, and a
        subscript's key."""
        if isinstance(e, TpyFieldAccess):
            self.require(e.factory_expansion is None)
            root = self.field_chain_root(e)
            # A class or enum named by its path is no storage to read.
            if not isinstance(root, TpyFieldAccess):
                self.place(root)
            return
        callee = e.record_getitem or e.getitem_callee
        if callee is not None:
            # A class's own `__getitem__`: the call it is, on its receiver.
            self.require(self.admitted(callee))
            self.walk(e.obj)
            self.index(e.index)
            return
        self.require(self.builtin_element_read(e))
        if e.slice_function_info is not None:
            self.require(self.admitted(e.slice_function_info))
        self.place(e.obj)
        self.index(e.index)

    def index(self, index: TpyExpr) -> None:
        if isinstance(index, TpySlice):
            for part in index.children():
                self.walk(part)
        else:
            self.walk(index)

    def visit(self, e: TpyExpr) -> bool:
        """Judge one node; True descends into its `children()`. A node judged
        as a whole -- a field chain, a call (whose callee name is a child),
        a subscript with its slice -- walks the parts it admits itself and
        answers False."""
        if isinstance(e, TpyCall):
            self.call(e)
            return False
        if isinstance(e, TpyMethodCall):
            self.method_call(e)
            return False
        self.on_node(e)
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
        if isinstance(e, (TpyFieldAccess, TpySubscript)):
            # What the read yields must be inert; the place it reads through
            # is only read.
            self.read_through(e)
            self.typed(e)
            return False
        if isinstance(e, TpyChainedCompare):
            self.require(e.pairs is not None)
            for part in e.pairs or ():
                self.walk(part)
            return False
        if isinstance(e, (TpyBinOp, TpyUnaryOp)):
            # Operand types inert leave no user dunder for the operator or a
            # `__contains__` to resolve to.
            self.typed(e)
            self.on_call(e)
            return True
        if isinstance(e, TpyFString):
            return True
        if isinstance(e, TpyNamedExpr):
            self.typed(e)
            self.rebind(e.target, self.ctx.get_expr_type(e))
            return True
        if isinstance(e, (TpyIfExpr, TpyArrayLiteral, TpyTupleLiteral,
                          TpySetLiteral, TpyDictLiteral, TpyListRepeat)):
            self.typed(e)
            return True
        if isinstance(e, (TpyListComprehension, TpySetComprehension,
                          TpyDictComprehension)):
            # Each clause iterates like a nested `for`; its filters and the
            # element are the children.
            self.require(self.admits_nested)
            for gen in e.generators:
                self.require(not gen.owns_elements and is_inert_type(
                    self.ctx.get_expr_type(gen.iterable)))
            self.typed(e)
            return True
        if isinstance(e, TpyVarargPack):
            self.require(is_inert_type(e.element_type))
            for child in e.args:
                self.walk(child)
            return False
        raise NotInert

    def call_args(self, args: list[TpyExpr], kwargs: dict[str, TpyExpr]) -> None:
        for a in args:
            self.walk(a)
        for a in kwargs.values():
            self.walk(a)

    def call(self, e: TpyCall) -> None:
        self.on_node(e)
        self.require(isinstance(e.func, TpyName)
                     and e.macro_expansion is None and e.dunder_call is None
                     and e.dyn_hasattr_call is None
                     and e.dyn_getattr_default_call is None
                     and e.kwarg_td_call is None
                     and e.double_star_unpack is None
                     and e.subscript_callee is None)
        if e.isinstance_type is not None or e.cast_target_type is not None:
            # A type test or a cast reads its subject; the type argument is
            # no value.
            self.walk(e.args[0] if e.isinstance_type is not None
                      else e.args[-1])
            self.typed(e)
            return
        if e.enum_from_value is not None:
            # An enum lookup by value: the subject decides.
            self.call_args(e.args, e.kwargs)
            self.typed(e)
            return
        self.require(e.resolved_function_info is not None
                     and self.admitted(e.resolved_function_info))
        self.call_args(e.args, e.kwargs)
        self.typed(e)
        self.on_call(e)

    def method_call(self, e: TpyMethodCall) -> None:
        self.on_node(e)
        if e.macro_expansion is not None:
            # The call is its expansion (`m.Color(1)` is `Color(1)`).
            self.walk(e.macro_expansion)
            return
        self.require(e.fstr_expansion is None
                     and not e.is_callable_field
                     and e.typed_dict_get_field is None
                     and e.deref_depth == 0 and e.super_parent_type is None
                     and e.unbound_self_parent_type is None
                     and e.subscript_callee is None
                     and e.double_star_unpack is None
                     and e.resolved_function_info is not None
                     and self.admitted(e.resolved_function_info))
        # A module function or a static method has no receiver value.
        if not (e.user_module_call or e.builtin_module_call or e.is_static_call):
            self.walk(e.obj)
        self.call_args(e.args, e.kwargs)
        self.typed(e)
        self.on_call(e)


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
    walk = InertWalk(ctx, func)
    # `super().__del__()` emits nothing: the parent's destructor runs after
    # this one as a body of its own, with its own fact.
    body = [s for s in func.body if not is_super_del_call(s)]
    try:
        walk_body_stmts(body, walk.walk, walk.stmt)
    except NotInert:
        fi.may_interrupt = True
        return
    fi.may_interrupt = False
