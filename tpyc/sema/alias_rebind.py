"""Alias-rebind storage pass.

Rebinding a reference-typed local to a fresh object (`p = Point(2)`) has two
sound renders: write the new object INTO the name's current storage, which
destroys the superseded object right there, as CPython's refcount drop does;
or give the site storage of its OWN, so an alias that still points at the
old object keeps it. The first is right whenever nothing can observe the
old object, the second whenever something can -- and only sema can tell.

This pass answers it per rebind site, after the body walk, by replaying the
body in program order: a forward dataflow over the AST with a fixpoint at
every loop, so a loan taken after the site on one iteration and a foreign
binding arriving over the back edge are both seen. Two facts flow:

* `origins`: for each name, the set of statements whose write may be the
  one its storage currently holds. Every rvalue bind is its own origin; a
  borrow of someone else's storage (an lvalue bind, a parameter, a loop
  variable, a captured or global name, `None`) is FOREIGN.
* `loans`: for each holder, the loans it carries -- (root, kind, origins of
  the root at bind time). The borrow tracker registers the loans during the
  walk and records them per statement (`BorrowTracker.stmt_loans`); the replay
  adds what the tracker does not model, on the side of "unknown means
  aliased": a holder copied from another holder inherits its loans, a
  frame-factory call keeps its arguments, and a pointer that leaves through
  a call argument is held for ever.

A site writes IN_PLACE when the name owns every possible current storage
and no holder's loan can point at it; otherwise OWN. A holder counts
while it is BOUND, read again or not: under CPython the object lives as
long as any name refers to it, and its `__del__` says so. Liveness
matters only to the warning -- the one clobber OWN storage cannot avoid,
a loan taken from a site's own slot and still READ after the site
re-executes on the next iteration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Iterable

from ..identity_map import IdentityMap
from ..parse import (
    TpyAssign, TpyBreak, TpyCall, TpyCoerce, TpyContinue, TpyDelVar,
    TpyExpr, TpyForEach, TpyFunction, TpyGlobal, TpyIf, TpyMatch,
    TpyMethodCall, TpyName, TpyNamedExpr, TpyNestedDef, TpyNoneLiteral,
    TpyRaise, TpyReturn, TpySlice, TpyStmt, TpySubscript, TpyTry,
    TpyTupleUnpack, TpyVarDecl, TpyWhile, TpyWith,
)
from ..parse.nodes import RebindStorage
from ..prescan import bound_names_of, walrus_names_of
from ..type_def_registry import (
    is_bytes_view_type, is_char_type, is_str_view_type,
)
from ..typesys import (
    NoneType, OptionalType, TpyType, UnionType, is_any_bytes_type,
    is_any_str_type, is_numeric_type, unwrap_readonly, view_family_for_type,
)
from ..value_category import frame_factory_callee, is_rvalue_source
from .context import (
    BorrowKind, ITER_BORROWER, _borrow_storage_roots, addr_taken_roots,
)

if TYPE_CHECKING:
    from .context import SemanticContext


# Origins that are not a statement of the body: storage the name does not
# own (FOREIGN), and "no binding reaches here on some path" (UNBOUND).
FOREIGN = -1
UNBOUND = -2
_NOT_OWNED = frozenset((FOREIGN, UNBOUND))

# Holder of every pointer that left through a call argument: never dies.
_ESCAPED = "__escaped"

# Names that take a pointer INTO their argument's storage rather than a copy.
_PTR_TAKING_CALLS = ("take_ptr", "Ptr")
_PTR_COERCIONS = ("record_to_ptr", "record_to_const_ptr",
                  "upcast_to_ptr", "upcast_to_const_ptr")

_Loan = tuple[str, BorrowKind, frozenset[int]]


class BindKind(Enum):
    """What a name binding puts in the name's storage -- recorded by sema for
    every binding of a name (var-decl, assign-to-name, walrus) in
    `ctx.func.bind_kinds`. RVALUE: a fresh object the name owns. LVALUE: a
    borrow of some other storage. NONE: the `None` literal (no object)."""
    RVALUE = "rvalue"
    LVALUE = "lvalue"
    NONE = "none"


# -- sema-side stamps ---------------------------------------------------------

def _peel(e: TpyExpr) -> TpyExpr:
    while isinstance(e, TpyCoerce):
        e = e.expr
    return e


def bind_kind_of(ctx: 'SemanticContext',
                 value: TpyExpr | None) -> BindKind | None:
    """What a binding puts in the name's storage; None for a bare
    declaration without an initializer."""
    if value is None:
        return None
    if isinstance(_peel(value), TpyNoneLiteral):
        return BindKind.NONE
    return (BindKind.RVALUE if is_rvalue_source(ctx, value)
            else BindKind.LVALUE)


def is_reference_local(var_type: TpyType | None) -> bool:
    """A local whose storage a rebind can overwrite under a live alias: a
    reference type. Value types are copied at the rebind; str/bytes have
    their own pinned-view tier."""
    if var_type is None:
        return False
    inner = unwrap_readonly(var_type)
    return not inner.is_value_type() and view_family_for_type(inner) is None


def stamp_bind_kind(ctx: 'SemanticContext', stmt: 'TpyVarDecl | TpyAssign',
                    name: str, value: TpyExpr | None,
                    var_type: TpyType | None, *, rebind: bool) -> None:
    """Record what the binding writes, and mark an rvalue REBIND of a
    reference local as a site the storage pass decides. The default is OWN
    -- the pass proves IN_PLACE."""
    kind = bind_kind_of(ctx, value)
    ctx.func.bind_kinds[stmt] = kind
    if rebind and kind is BindKind.RVALUE and is_reference_local(var_type):
        stmt.rebind_storage = RebindStorage.OWN
        ctx.func.gate_sites.add(stmt)


def global_write_facts(
        funcs: Iterable[TpyFunction]) -> tuple[set[str], dict[str, str]]:
    """The module's two `global` facts, from one walk over every body.

    `declared`: every name some body declares `global`, read-only
    declarations included. Their module-level storage can change between two
    module-level statements, so the module-init replay treats them as
    foreign.

    `rebound`: the globals some function actually REBINDS, each mapped to
    that function. A `global X` only declares which storage a write in that
    body targets; a body that never binds X merely READS the module slot, so
    it is not a rebind. Every nested def is its own scope: its `global`
    declarations pair with its own writes, not the enclosing body's, and it
    is reported under the enclosing function the user can find.

    The rebind is what makes a module global un-borrowable across calls --
    it reseats the slot's storage, freeing the buffer every outstanding view
    or reference points into -- so the fact must be complete before any body
    is analyzed, which is why it is decided here from the AST rather than
    accumulated as the `global` statements are analyzed. That places it
    before `@function_macro` expansion, so a `global X` write a macro
    introduces is invisible to it (BUGS.md#rebound-global-borrow-open-sinks).
    """
    declared_any: set[str] = set()
    rebound: dict[str, str] = {}

    def scope(stmts: list[TpyStmt], owner: str) -> None:
        declared: set[str] = set()
        bound: set[str] = set()
        walk(stmts, declared, bound, owner)
        declared_any.update(declared)
        for name in declared & bound:
            rebound.setdefault(name, owner)

    def walk(stmts: list[TpyStmt], declared: set[str], bound: set[str],
             owner: str) -> None:
        for s in stmts:
            if isinstance(s, TpyGlobal):
                declared.update(s.names)
            elif isinstance(s, TpyNestedDef):
                scope(s.func.body, owner)
                continue
            else:
                bound.update(bound_names_of(s))
                bound.update(walrus_names_of(s))
            for body in s.sub_bodies():
                walk(body, declared, bound, owner)

    for f in funcs:
        scope(f.body, f.name)
    return declared_any, rebound


# -- the replay ---------------------------------------------------------------

@dataclass
class _State:
    origins: dict[str, frozenset[int]] = field(default_factory=dict)
    loans: dict[str, frozenset[_Loan]] = field(default_factory=dict)

    def copy(self) -> '_State':
        return _State(dict(self.origins), dict(self.loans))


def _join(a: _State | None, b: _State | None) -> _State | None:
    """Union of two reaching states. A name bound on one side only is
    UNBOUND on the other -- that path reaches with no storage at all."""
    if a is None:
        return None if b is None else b.copy()
    if b is None:
        return a.copy()
    out = a.copy()
    for k, v in b.origins.items():
        out.origins[k] = out.origins.get(k, frozenset((UNBOUND,))) | v
    for k in a.origins.keys() - b.origins.keys():
        out.origins[k] = out.origins[k] | {UNBOUND}
    for k, v in b.loans.items():
        out.loans[k] = out.loans.get(k, frozenset()) | v
    return out


def _join_all(states: Iterable[_State | None]) -> _State | None:
    out: _State | None = None
    for s in states:
        out = _join(out, s)
    return out


@dataclass
class _Loop:
    breaks: list[_State] = field(default_factory=list)
    continues: list[_State] = field(default_factory=list)


class _Replay:
    def __init__(self, ctx: 'SemanticContext', always_foreign: set[str]):
        self.ctx = ctx
        self.bind_kinds = ctx.func.bind_kinds
        self.stmt_loans = ctx.func.borrow_tracker.stmt_loans
        self.gate_sites = ctx.func.gate_sites
        self.always_foreign = always_foreign
        self.ids: IdentityMap = IdentityMap()
        self.by_id: dict[int, object] = {}
        self.site_states: IdentityMap = IdentityMap()
        self.sites: list[TpyVarDecl | TpyAssign] = []
        self.loops: list[_Loop] = []
        self.try_sinks: list[list[_State]] = []
        self.bound_here: set[str] = set()

    def id_of(self, node: object) -> int:
        n = self.ids.get(node)
        if n is None:
            n = len(self.by_id)
            self.ids[node] = n
            self.by_id[n] = node
        return n

    # -- statements --

    def walk_stmts(self, stmts: list[TpyStmt],
                   st: _State | None) -> _State | None:
        for s in stmts:
            if st is None:
                return None
            st = self.walk_stmt(s, st)
            if st is not None:
                for sink in self.try_sinks:
                    sink.append(st.copy())
        return st

    def walk_stmt(self, s: TpyStmt, st: _State) -> _State | None:
        self.bound_here = set()
        if isinstance(s, TpyVarDecl):
            self.bind(s, s.name, s.init, self.bind_kinds.get(s), st, s)
            return st
        if isinstance(s, TpyAssign):
            if isinstance(s.target, TpyName):
                self.bind(s, s.target.name, s.value, self.bind_kinds.get(s),
                          st, s)
            else:
                self.expr_effects(s.value, st, s)
                self.expr_effects(s.target, st, s)
            return st
        if isinstance(s, TpyTupleUnpack):
            self.expr_effects(s.value, st, s, top_is_bind=True)
            for name in s.targets:
                if name is not None:
                    self.bind_foreign(s, name, s.value, st)
            return st
        if isinstance(s, TpyForEach):
            return self.walk_for(s, st)
        if isinstance(s, TpyWhile):
            return self.walk_while(s, st)
        if isinstance(s, TpyIf):
            self.expr_effects(s.condition, st, s)
            then = self.walk_stmts(s.then_body, st.copy())
            else_ = self.walk_stmts(s.else_body, st.copy())
            return _join(then, else_)
        if isinstance(s, TpyMatch):
            return self.walk_match(s, st)
        if isinstance(s, TpyWith):
            for item in s.items:
                self.expr_effects(item.context_expr, st, s)
                if item.target is not None:
                    self.bind_foreign(s, item.target, item.context_expr, st)
            return self.walk_stmts(s.body, st)
        if isinstance(s, TpyTry):
            return self.walk_try(s, st)
        if isinstance(s, TpyDelVar):
            for name in s.names:
                st.origins[name] = frozenset()
                st.loans.pop(name, None)
            return st
        if isinstance(s, (TpyReturn, TpyRaise)):
            for e in s.exprs():
                self.expr_effects(e, st, s)
            return None
        if isinstance(s, TpyBreak):
            if self.loops:
                self.loops[-1].breaks.append(st.copy())
            return None
        if isinstance(s, TpyContinue):
            if self.loops:
                self.loops[-1].continues.append(st.copy())
            return None
        if isinstance(s, TpyNestedDef):
            # A separate scope with its own replay; the def binds a
            # callable, not a reference local.
            return st
        for e in s.exprs():
            self.expr_effects(e, st, s)
        self.bind_other_holders(s, st)
        return st

    def walk_loop_body(self, s: TpyStmt, st: _State,
                       head_effects) -> tuple[_State, _Loop]:
        """Fixpoint over a loop body. Returns the head state (what reaches
        the loop test on any iteration) and the loop's break/continue
        states. `head_effects(state)` applies what the head evaluates on
        every iteration (the condition, the loop variable bind)."""
        head = st
        loop = _Loop()
        for _ in range(64):
            loop = _Loop()
            body_in = head.copy()
            head_effects(body_in)
            self.loops.append(loop)
            out = self.walk_stmts(s.body, body_in)
            self.loops.pop()
            new_head = _join_all([st, out, *loop.continues])
            assert new_head is not None
            if (new_head.origins == head.origins
                    and new_head.loans == head.loans):
                return head, loop
            head = new_head
        raise AssertionError("alias-rebind loop replay did not converge")

    def walk_for(self, s: TpyForEach, st: _State) -> _State | None:
        self.expr_effects(s.iterable, st, s)
        groups = self.groups(s)
        iter_key = f"{ITER_BORROWER}#{self.id_of(s)}"
        iter_loans = self.loans_of(iter_key, groups, None, st)
        if iter_loans:
            st.loans[iter_key] = iter_loans

        def at_head(state: _State) -> None:
            self.bind_foreign(s, s.var, s.iterable, state)

        head, loop = self.walk_loop_body(s, st, at_head)
        exit_st = head.copy()
        exit_st.loans.pop(iter_key, None)
        for b in loop.breaks:
            b.loans.pop(iter_key, None)
        out = self.walk_stmts(s.orelse, exit_st)
        return _join_all([out, *loop.breaks])

    def walk_while(self, s: TpyWhile, st: _State) -> _State | None:
        def at_head(state: _State) -> None:
            self.expr_effects(s.condition, state, s)

        head, loop = self.walk_loop_body(s, st, at_head)
        exit_st = head.copy()
        at_head(exit_st)
        out = self.walk_stmts(s.orelse, exit_st)
        return _join_all([out, *loop.breaks])

    def walk_match(self, s: TpyMatch, st: _State) -> _State | None:
        self.expr_effects(s.subject, st, s)
        groups = self.groups(s)
        outs: list[_State | None] = [] if s.is_exhaustive else [st.copy()]
        for case in s.cases:
            cs = st.copy()
            # Which arm a capture belongs to is not recorded; binding every
            # arm's captures into each arm over-approximates harmlessly.
            for holder in groups:
                cs.loans[holder] = self.loans_of(holder, groups, None, cs)
                cs.origins[holder] = frozenset((FOREIGN,))
            if case.guard is not None:
                self.expr_effects(case.guard, cs, s)
            outs.append(self.walk_stmts(case.body, cs))
        return _join_all(outs)

    def walk_try(self, s: TpyTry, st: _State) -> _State | None:
        # A handler can start after any statement of the try body, at any
        # depth, so its entry is the union of every state the body reached.
        sink: list[_State] = []
        self.try_sinks.append(sink)
        try_out = self.walk_stmts(s.try_body, st.copy())
        self.try_sinks.pop()
        raised = _join_all([st, *sink])
        assert raised is not None
        handler_outs: list[_State | None] = []
        for h in s.handlers:
            hs = raised.copy()
            if h.binding is not None:
                hs.origins[h.binding] = frozenset((FOREIGN,))
                hs.loans.pop(h.binding, None)
            handler_outs.append(self.walk_stmts(h.body, hs))
        normal = (self.walk_stmts(s.else_body, try_out)
                  if try_out is not None else None)
        after = _join_all([normal, *handler_outs])
        if not s.finally_body:
            return after
        # The finally runs on every path, exceptional ones included: its
        # sites see everything the try reached.
        fin_in = _join_all([after, raised])
        fin_out = self.walk_stmts(s.finally_body, fin_in)
        return None if after is None else fin_out

    # -- bindings --

    def groups(self, stmt: TpyStmt) -> dict[str, list[tuple[str, BorrowKind]]]:
        out: dict[str, list[tuple[str, BorrowKind]]] = {}
        for storage, holder, kind in self.stmt_loans.get(stmt, ()):
            out.setdefault(holder, []).append((storage, kind))
        return out

    def bind(self, stmt: TpyStmt, name: str, value: TpyExpr | None,
             kind: BindKind | None, st: _State, node: object) -> None:
        if value is not None:
            self.expr_effects(value, st, stmt, top_is_bind=True)
        site = getattr(stmt, "rebind_storage", None) is not None and node is stmt
        # A first bind inside a loop body runs again on the next iteration
        # and reuses its storage like any rebind. Nothing before it can be
        # written in place, so it is no storage decision -- but a holder
        # still reading the previous iteration's object is the same
        # clobber question, asked of the same site.
        if (not site and node is stmt and self.loops
                and kind is BindKind.RVALUE
                and is_reference_local(self.local_type(name, value))):
            site = True
        if site:
            prev = self.site_states.get(stmt)
            if prev is None:
                self.sites.append(stmt)  # type: ignore[arg-type]
            self.site_states[stmt] = _join(prev, st)
        st.loans[name] = self.loans_of(name, self.groups(stmt), value, st)
        if kind is BindKind.RVALUE and name not in self.always_foreign:
            st.origins[name] = frozenset((self.id_of(node),))
        else:
            st.origins[name] = frozenset((FOREIGN,))
        self.bound_here.add(name)

    def bind_foreign(self, stmt: TpyStmt, name: str, source: TpyExpr | None,
                     st: _State) -> None:
        st.loans[name] = self.loans_of(name, self.groups(stmt), source, st)
        st.origins[name] = frozenset((FOREIGN,))
        self.bound_here.add(name)

    def bind_other_holders(self, stmt: TpyStmt, st: _State) -> None:
        """Loans registered on a statement for a holder the statement's own
        shape did not bind (a capture, a target bound by a helper)."""
        for holder in self.groups(stmt):
            if holder not in self.bound_here and holder != ITER_BORROWER:
                self.bind_foreign(stmt, holder, None, st)

    def loans_of(self, holder: str, groups: dict[str, list[tuple[str, BorrowKind]]],
                 value: TpyExpr | None, st: _State) -> frozenset[_Loan]:
        out: set[_Loan] = set()
        for root, kind in groups.get(holder, ()):
            out.add((root, kind, self.origins_of(root, st)))
        if value is None:
            return frozenset(out)
        inner = _peel(value)
        # A holder bound from another holder carries that holder's loans:
        # a copied Ptr, an alias of an alias, a view read off a pointer.
        if not self.carries_no_borrow(value):
            for n in _read_names(inner):
                if n in st.loans:
                    out |= st.loans[n]
        # A generator or coroutine object keeps its reference arguments
        # and receiver for as long as it lives.
        if (isinstance(inner, (TpyCall, TpyMethodCall))
                and frame_factory_callee(inner.resolved_function_info)):
            srcs = list(inner.args)
            if isinstance(inner, TpyMethodCall):
                srcs.insert(0, inner.obj)
            for src in srcs:
                for root in _borrow_storage_roots(src):
                    if root != holder:
                        out.add((root, BorrowKind.OPAQUE,
                                 self.origins_of(root, st)))
        return frozenset(out)

    def origins_of(self, root: str, st: _State) -> frozenset[int]:
        base = root.split(".", 1)[0]
        return st.origins.get(base, frozenset((FOREIGN,)))

    def carries_no_borrow(self, value: TpyExpr) -> bool:
        """A bound value that cannot hold a pointer into anything: a
        scalar, or an owned string/bytes."""
        t = self.ctx.get_expr_type(value)
        if t is None:
            return False
        t = unwrap_readonly(t)
        if isinstance(t, NoneType) or is_numeric_type(t) or is_char_type(t):
            return True
        if is_any_str_type(t) and not is_str_view_type(t):
            return True
        return is_any_bytes_type(t) and not is_bytes_view_type(t)

    # -- expressions --

    def expr_effects(self, e: TpyExpr, st: _State, stmt: TpyStmt,
                     top_is_bind: bool = False) -> None:
        """Replay a walrus binding and a pointer escaping through a call
        argument, in evaluation order. The top node of a bind is the
        holder's own loan, registered by the tracker, not an escape."""
        inner = _peel(e)
        if isinstance(inner, TpyNamedExpr):
            self.bind(stmt, inner.target, inner.value,
                      self.bind_kinds.get(inner), st, inner)
            return
        if not top_is_bind:
            for root in _ptr_escape_roots(e):
                st.loans[_ESCAPED] = st.loans.get(_ESCAPED, frozenset()) | {
                    (root, BorrowKind.PTR, self.origins_of(root, st))}
        for child in inner.children():
            self.expr_effects(child, st, stmt)

    # -- the decision --

    def decide(self) -> None:
        """Set IN_PLACE on every stamped site the replay proves, and warn
        on the residual clobber at every site. The two are separate
        questions: a site some path reaches with storage it does not own
        stays OWN, yet its own slot is still reused when it runs again,
        and a holder that still reads the previous object is clobbered
        all the same."""
        for stmt in self.sites:
            name = stmt.name if isinstance(stmt, TpyVarDecl) else stmt.target.name  # type: ignore[union-attr]
            value = stmt.init if isinstance(stmt, TpyVarDecl) else stmt.value
            st = self.site_states.get(stmt)
            if st is None:
                continue  # unreachable: OWN
            # None: liveness never walked the body -- decide, but stay quiet.
            live = stmt.live_names_after
            origins = st.origins.get(name)
            if not origins:
                continue
            var_type = self.local_type(name, value)
            decidable = (getattr(stmt, "rebind_storage", None) is not None
                         and not (origins & _NOT_OWNED)
                         and not self.in_place_unrenderable(var_type, origins))
            own = False
            clobbered: dict[str, BorrowKind] = {}
            prefix = name + "."
            site_id = self.ids.get(stmt)
            # `outer = p` with `p` bound in the loop body is diagnosed at the
            # alias site by the scope-escape check, which hoists `p` for it;
            # the clobber this site would report for THAT holder is the same
            # alias. Keyed on the pair: another holder of `p` is not covered.
            escape_warned = self.ctx.func.escape_warned_aliases
            for holder, loans in st.loans.items():
                immortal = (holder == _ESCAPED
                            or holder.startswith(ITER_BORROWER))
                read_again = live is not None and holder in live
                for root, kind, lorigins in loans:
                    if root != name and not root.startswith(prefix):
                        continue
                    if not (lorigins & origins):
                        continue
                    own = True
                    # A loan on this very site's storage that is still read
                    # after the site runs again: one slot cannot hold both.
                    if (read_again and not immortal
                            and kind is not BorrowKind.OPAQUE
                            and site_id in lorigins
                            and not (kind is BorrowKind.ALIAS
                                     and (holder, name) in escape_warned)):
                        clobbered[holder] = kind
            if decidable and not own:
                stmt.rebind_storage = RebindStorage.IN_PLACE
            if clobbered:
                self.warn(stmt, name, var_type, clobbered)

    def local_type(self, name: str, value: TpyExpr | None) -> TpyType | None:
        """The local's DECLARED slot type: a rebind's own rvalue answers the
        narrower question "what is being stored", and `in_place_unrenderable`
        asks the wider one -- what the slot may hold at the site."""
        t = self.ctx.local_decl_type(name)
        if t is None and value is not None:
            t = self.ctx.get_expr_type(value)
        return t

    def in_place_unrenderable(self, var_type: TpyType | None,
                              origins: frozenset[int]) -> bool:
        """Shapes no in-place write exists for: a pointer-variant union
        (the current alternative may not be the new member's type) and an
        Optional whose current storage may be empty."""
        if var_type is None:
            return True
        inner = unwrap_readonly(var_type)
        if isinstance(inner, UnionType):
            return True
        if not isinstance(inner, OptionalType):
            return False
        for o in origins:
            node = self.by_id[o]
            src = node.init if isinstance(node, TpyVarDecl) else node.value  # type: ignore[union-attr]
            vt = self.ctx.get_expr_type(src) if src is not None else None
            if vt is None or isinstance(unwrap_readonly(vt), OptionalType):
                return True
        return False

    def warn(self, stmt: TpyStmt, name: str, var_type: TpyType | None,
             clobbered: dict[str, BorrowKind]) -> None:
        alias = sorted(clobbered)[0]
        if var_type is not None and self.ctx.is_type_nocopy(var_type):
            fix = (f"both names share its storage and '{name}' cannot be "
                   f"copied; bind the new value to a name of its own")
        elif clobbered[alias] is BorrowKind.ALIAS:
            fix = (f"both names share its storage; bind '{alias}' with "
                   f"copy({name}), or bind the new value to a name of its own")
        else:
            # The loan points INTO the object, so `copy(name)` is not the
            # spelling that detaches it -- name only the remedy that always is.
            fix = ("both names share its storage; bind the new value to a name "
                   "of its own")
        self.ctx.warning(
            f"'{alias}' will not keep the object it was given -- '{name}' is "
            f"rebound here and {fix}",
            stmt)


def _read_names(e: TpyExpr) -> set[str]:
    out: set[str] = set()
    stack = [e]
    while stack:
        n = stack.pop()
        if isinstance(n, TpyName):
            out.add(n.name)
        stack.extend(n.children())
    return out


def _ptr_escape_roots(e: TpyExpr) -> list[str]:
    """Storage roots a pointer or view into them is minted from by this
    node: `take_ptr(x)` / `Ptr(x)`, a record-to-pointer coercion, a slice
    view. Where such a node is not the whole value of a binding, the pointer
    went somewhere the tracker cannot see (a call argument, a container),
    so its root is held for the rest of the body."""
    if isinstance(e, TpyCoerce) and e.coercion.name in _PTR_COERCIONS:
        return addr_taken_roots(e.expr)
    inner = _peel(e)
    if isinstance(inner, TpyCall) and inner.args:
        fn = inner.func
        if isinstance(fn, TpyName) and fn.name in _PTR_TAKING_CALLS:
            return addr_taken_roots(inner.args[0])
        fi = inner.resolved_function_info
        if fi is not None and fi.value_ptr_coercion:
            return addr_taken_roots(inner.args[0])
        if inner.call_type is not None and inner.call_type.is_pointer():
            return addr_taken_roots(inner.args[0])
    if isinstance(inner, TpySubscript) and isinstance(inner.index, TpySlice):
        return addr_taken_roots(inner.obj)
    return []


def decide_rebind_storage(ctx: 'SemanticContext', stmts: list[TpyStmt], *,
                          always_foreign: set[str] = frozenset(),
                          initial_foreign: set[str] = frozenset()) -> None:
    """Decide `rebind_storage` for every stamped site of a body and warn on
    the residual clobber. `initial_foreign` names storage the body starts
    out borrowing (parameters); `always_foreign` names storage it never
    owns (nonlocal / global declarations)."""
    replay = _Replay(ctx, set(always_foreign))
    st = _State()
    for name in initial_foreign:
        st.origins[name] = frozenset((FOREIGN,))
    replay.walk_stmts(stmts, st)
    replay.decide()
    # The per-name verdict the frame layout consumes: a local with any site
    # left OWN (decided, or never reached) goes pointer-form on a frame.
    ctx.func.own_rebind_names = frozenset(
        s.name if isinstance(s, TpyVarDecl) else s.target.name  # type: ignore[union-attr]
        for s in replay.gate_sites
        if s.rebind_storage is RebindStorage.OWN)
