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
  the root at bind time, and for a tuple root the element the loan points
  into). The borrow tracker registers the loans during the
  walk and records them per statement (`BorrowTracker.stmt_loans`); the replay
  adds what the tracker does not model, on the side of "unknown means
  aliased": a holder copied from another holder inherits its loans, a
  generator or coroutine call keeps what its frame borrows
  (`frame_borrowed_operands`) and any call result what its callee's
  recorded return borrows name, and a pointer that leaves through a call
  argument and a frame handed to an `Own` parameter are held for ever. A
  name holding a generator is never rebound: a rebind site of one that some
  path reaches with a generator already bound is an error.

A site writes IN_PLACE when the name owns every possible current storage
and no holder's loan can point at it; otherwise OWN. A holder counts
while it is BOUND, read again or not: under CPython the object lives as
long as any name refers to it, and its `__del__` says so. Liveness
matters only to the warning -- the one clobber OWN storage cannot avoid,
a loan taken from a site's own slot and still READ after the site
re-executes on the next iteration.

A tuple binding is a site when it holds a reference element of its own
(`tuple_elem_owned`): the scalar's question asked per element. Its rebind
replaces only what the old tuple owned, so a loan clashes when the element
it points into is one an origin owned -- a loan through a borrowed element
points outside the tuple and never does.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Container, Iterable, Mapping, NamedTuple

from ..diagnostics import SemanticError
from ..identity_map import IdentityMap
from ..parse import (
    TpyAssign, TpyAwait, TpyBreak, TpyCall, TpyCoerce, TpyContinue, TpyDelVar,
    TpyExpr, TpyFieldAccess, TpyForEach, TpyFunction,
    TpyGlobal, TpyIf, TpyIfExpr, TpyLambda, TpyMatch,
    TpyMethodCall, TpyName, TpyNamedExpr, TpyNestedDef, TpyNoneLiteral,
    TpyRaise, TpyReturn, TpySlice, TpyStmt, TpySubscript, TpyTry,
    TpyTupleLiteral, TpyTupleUnpack, TpyVarDecl, TpyWhile, TpyWith,
    TupleElemCapture,
)
from ..parse.nodes import (RebindStorage, read_names, value_capture_lambdas,
                           walk_body_stmts)
from ..prescan import (bound_names_of, loop_bindings_of, scope_bound_names,
                       walrus_names_of)
from ..type_def_registry import (
    is_bytes_view_type, is_char_type, is_str_view_type,
)
from ..typesys import (
    NoneType, OptionalType, OwnType, ReadonlyType, TpyType, TupleType,
    UnionType,
    is_any_bytes_type,
    recorded_return_borrow_sources,
    is_any_str_type, is_numeric_type, unwrap_own, unwrap_readonly,
    unwrap_ref_type, unwrap_send_sync, view_family_for_type,
)
from ..value_category import binds_owned_value, call_returns_cpp_ref
from .context import (
    BorrowKind, ITER_BORROWER, LoanInfo, _borrow_storage_roots,
    _root_name_of_expr,
    addr_taken_roots, call_borrow_operands, call_contract_lend_exprs,
    call_lend_sources,
    element_index_key,
    call_param_args, frame_binding_calls, frame_borrowed_operands,
)
from .loop_frames import (
    LoopFrameHold, WithExitCheck, binding_value, check_loop_hold,
    StorageEdges, resolve_source_writes, storage_closure, storage_edges,
)

if TYPE_CHECKING:
    from .compatibility import TypeCompatibility
    from .context import SemanticContext


# Origins that are not a statement of the body: storage the name does not
# own (FOREIGN), and "no binding reaches here on some path" (UNBOUND).
FOREIGN = -1
UNBOUND = -2
_NOT_OWNED = frozenset((FOREIGN, UNBOUND))

# Holder of every pointer that left through a call argument: never dies.
_ESCAPED = "__escaped"
# The holder a tuple site reports when only a handed-out pointer or an
# iterator still reaches its object (`tuple_own_sites`).
ESCAPED_HOLDER = _ESCAPED

# Names that take a pointer INTO their argument's storage rather than a copy.
_PTR_TAKING_CALLS = ("take_ptr", "Ptr")
_PTR_COERCIONS = ("record_to_ptr", "record_to_const_ptr",
                  "upcast_to_ptr", "upcast_to_const_ptr")

class _Loan(NamedTuple):
    """One loan a holder carries: the root it is on, its kind, the origins
    of the root when it was taken, and -- for a tuple root -- which element
    of it the loan points into (None: the whole tuple, or an element no
    literal index names)."""
    root: str
    kind: BorrowKind
    origins: frozenset[int]
    elem: int | None = None


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


def _reference_elements(slot: TpyType | None) -> tuple[bool, ...] | None:
    """Per element of a tuple slot, whether it is a reference type; None
    when the slot is no tuple or holds no reference element at all."""
    bare = (unwrap_readonly(unwrap_ref_type(unwrap_own(slot)))
            if slot is not None else None)
    if not isinstance(bare, TupleType):
        return None
    ref = tuple(not unwrap_readonly(unwrap_ref_type(unwrap_own(et)))
                .is_value_type() for et in bare.element_types)
    return ref if any(ref) else None


def tuple_elem_owned(ctx: 'SemanticContext', value: TpyExpr | None,
                     slot: TpyType | None) -> tuple[bool, ...] | None:
    """Per element of a tuple binding, whether the binding holds that
    element as an object of its own rather than a borrow of storage
    elsewhere: a literal's VALUE-captured reference elements; the `Own`
    positions of a call's declared return, every reference element of
    `-> Own[tuple[...]]`. A name, a borrowing call, a select and `None`
    own nothing. None when the slot holds no reference element (a value
    tuple is copied at every binding, so no rebind can clobber it)."""
    ref = _reference_elements(slot)
    if ref is None or value is None:
        return None
    init = _peel(value)
    owned: tuple[bool, ...] = (False,) * len(ref)
    if isinstance(init, TpyTupleLiteral) and init.elem_capture:
        caps = init.elem_capture
        owned = tuple(r and i < len(caps) and caps[i] is TupleElemCapture.VALUE
                      for i, r in enumerate(ref))
    elif isinstance(init, (TpyCall, TpyMethodCall)):
        fi = init.resolved_function_info
        rt = (unwrap_readonly(unwrap_send_sync(fi.return_type))
              if fi is not None and fi.return_type is not None else None)
        if rt is not None and not call_returns_cpp_ref(ctx, fi):
            if isinstance(rt, OwnType) and isinstance(
                    unwrap_readonly(rt.wrapped), TupleType):
                owned = ref
            elif (isinstance(rt, TupleType)
                    and len(rt.element_types) == len(ref)):
                owned = tuple(r and isinstance(unwrap_readonly(et), OwnType)
                              for r, et in zip(ref, rt.element_types))
    return owned


def tuple_name_bindings(ctx: 'SemanticContext', func: TpyFunction,
                        name: str) -> 'list[TpyVarDecl | TpyAssign] | None':
    """Every binding of `name` in a function body, nested blocks included
    and nested defs not; None when something other than a plain name
    binding binds it (an unpack, a loop, a `with`, a walrus). Read off the
    syntax alone, so it answers the same whenever it is asked: one walk per
    function, shared by every name and both layers. An `except` target is
    no binding of the name -- the handler declares one of its own -- and a
    match capture writing the binding is refused where it is lowered."""
    table = ctx.tuple_bindings_by_func.get(func)
    if table is None:
        table = _name_bindings(func.body)
        ctx.tuple_bindings_by_func[func] = table
    return table.get(name, [])


def _name_bindings(body: list[TpyStmt]
                   ) -> 'dict[str, list[TpyVarDecl | TpyAssign] | None]':
    """`tuple_name_bindings` for every name of a body at once."""
    out: dict[str, list['TpyVarDecl | TpyAssign'] | None] = {}

    def add(name: str, s: 'TpyVarDecl | TpyAssign') -> None:
        found = out.setdefault(name, [])
        if found is not None:
            found.append(s)

    def walk(stmts: list[TpyStmt]) -> None:
        for s in stmts:
            if isinstance(s, TpyNestedDef):
                continue
            if isinstance(s, TpyVarDecl):
                add(s.name, s)
            elif (isinstance(s, TpyAssign)
                    and isinstance(s.target, TpyName)):
                add(s.target.name, s)
            elif not isinstance(s, TpyDelVar):
                for n in bound_names_of(s):
                    out[n] = None
            for n in walrus_names_of(s):
                out[n] = None
            for sub in s.sub_bodies():
                walk(sub)

    walk(body)
    return out


def rebinds_owning_tuple(ctx: 'SemanticContext', func: 'TpyFunction | None',
                         name: str, reassigned: 'Iterable[str]', *,
                         excluded: 'Iterable[str]' = ()) -> bool:
    """A tuple local of a plain function body that the body rebinds and
    that holds a reference element of its own at some binding (its
    `tuple_elem_owned`): a pointer tuple that owns, each owned element the
    scalar reference local at its site. Not a parameter or a `nonlocal`
    (`excluded`), a frame's local, or a name some other statement kind
    binds. It reads the syntax and the bindings' ownership stamps only, so
    asked during the body walk it sees the bindings stamped so far: a yes
    then is final, a no may still turn into one."""
    if (func is None or func.is_generator or func.is_async
            or name not in reassigned or name in excluded):
        return False
    bindings = tuple_name_bindings(ctx, func, name)
    return bool(bindings) and any(any(ctx.tuple_elem_owned.get(b) or ())
                                  for b in bindings)


# A place: the name its path starts at, and the element of it a literal
# index names (None: the whole value).
_Place = tuple[str, int | None]


def _kept_places(value: TpyExpr) -> list[tuple[TpyExpr, _Place | None]]:
    """The operands a value about to be bound keeps a reference into --
    each arm of a ternary, each element of a tuple literal but a VALUE one
    (a fresh object or a copy, made before anything is rebound), a call
    through what it may lend under its contract (not what its body is
    proven to return: the answer must not depend on whether the callee's
    body was analyzed first) -- each with the place it is, None for a
    shape that is no place."""
    out: list[tuple[TpyExpr, _Place | None]] = []

    def leaf(e: TpyExpr) -> None:
        e = _peel(e)
        if isinstance(e, (TpyCall, TpyMethodCall)):
            for src in call_contract_lend_exprs(e):
                leaf(src)
            return
        p, elem = e, None
        while isinstance(p, (TpySubscript, TpyFieldAccess)):
            key = (element_index_key(p.index)
                   if isinstance(p, TpySubscript) else None)
            elem = (key[1] if key is not None and key[0] == "lit"
                    and isinstance(key[1], int) else None)
            p = _peel(p.obj)
        out.append((e, (p.name, elem) if isinstance(p, TpyName) else None))

    inner = _peel(value)
    for arm in ((inner.then_expr, inner.else_expr)
                if isinstance(inner, TpyIfExpr) else (inner,)):
        arm = _peel(arm)
        if not isinstance(arm, TpyTupleLiteral):
            leaf(arm)
            continue
        caps = arm.elem_capture
        for i, el in enumerate(arm.elements):
            if not (i < len(caps) and caps[i] is TupleElemCapture.VALUE):
                leaf(el)
    return out


def kept_elems(holder: str, value: TpyExpr) -> frozenset[int | None]:
    """The elements of `holder`'s current tuple that the value about to
    be bound to it keeps a reference into (`_kept_places`). The tracker
    files no loan whose root is its own holder, so a rebind that keeps
    what it replaces (`p = mixed(9, p[0])`, `p = (Box(9), p[0])`) is seen
    only here. An operand that is no place (`*p`, an operator) keeps the
    whole tuple when it mentions `holder` at all: unknown is kept, never
    "keeps nothing"."""
    return frozenset(
        place[1] if place is not None else None
        for e, place in _kept_places(value)
        if (place[0] == holder if place is not None
            else holder in read_names(e)))


def binding_roots(ctx: 'SemanticContext',
                  value: TpyExpr) -> tuple[str, ...] | None:
    """The names whose storage a binding's value points into
    (`_kept_places`), in source order, an operand that cannot hold a
    pointer (`carries_no_borrow`) left out. None when an operand is no
    place, or when no operand is left: nothing then says where the
    pointers point."""
    roots: dict[str, None] = {}
    for e, place in _kept_places(value):
        if carries_no_borrow(ctx, e):
            continue
        if place is None:
            return None
        roots[place[0]] = None
    return tuple(roots) or None


def _alias_sources(value: TpyExpr | None) -> 'list[str] | None':
    """The names a binding's value is when it is a plain name or a ternary
    of them, else None."""
    e = _peel(value) if value is not None else None
    if isinstance(e, TpyName):
        return [e.name]
    if isinstance(e, TpyIfExpr):
        a = _alias_sources(e.then_expr)
        b = _alias_sources(e.else_expr)
        return a + b if a is not None and b is not None else None
    return None


class _Form(Enum):
    """What one binding of a tuple name does with the set of pointer
    tuples (`_binding_form`)."""
    OWNS = "owns"
    READS = "reads"
    POINTS = "points"


def _binding_form(ctx: 'SemanticContext', b: 'TpyVarDecl | TpyAssign',
                  members: 'Container[str] | None') -> _Form:
    """OWNS: the binding holds an element of its own -- or carries no
    ownership stamp (`tuple_elem_owned`), which counts as owning. READS: it
    owns nothing and is a member of `members`, or a ternary of members
    (None: the set is not known yet, so every name may be one). POINTS:
    it owns nothing and reads no member, so where it points is the root
    rule's to judge (`_RootRule`)."""
    value = binding_value(b)
    owned = ctx.tuple_elem_owned.get(b)
    if value is None or owned is None or any(owned):
        return _Form.OWNS
    srcs = _alias_sources(value)
    if srcs is not None and (members is None
                             or all(x in members for x in srcs)):
        return _Form.READS
    return _Form.POINTS


def carries_no_borrow(ctx: 'SemanticContext', value: TpyExpr) -> bool:
    """A value that cannot hold a pointer into anything: a scalar, or an
    owned string/bytes."""
    t = ctx.get_expr_type(value)
    if t is None:
        return False
    t = unwrap_readonly(t)
    if isinstance(t, NoneType) or is_numeric_type(t) or is_char_type(t):
        return True
    if is_any_str_type(t) and not is_str_view_type(t):
        return True
    return is_any_bytes_type(t) and not is_bytes_view_type(t)


class _RootRule(NamedTuple):
    """Where a pointer tuple's binding that owns nothing may point in
    `func` (`refusal`). `bound`: every name its own scope binds
    (`scope_bound_names`: a match capture, an `except` target and `del`
    included, a `nonlocal` or `global` name not). `captures` (the names a
    nested def reads from its enclosing functions,
    `TpyNestedDef.captured_names`) only tells two refused kinds apart: a
    free name it does not capture is a module global (a body cannot rebind
    a global holding a reference)."""
    func: TpyFunction
    bound: frozenset[str]
    params: 'Mapping[str, object]'
    nested_rebinds: frozenset[str]
    captures: frozenset[str]

    def refusal(self, holder: str, name: str) -> str | None:
        """None when a binding of `holder` may point into `name`'s
        storage: a parameter of `func` or `self`, its own receiver --
        storage that encloses every holder, so long as nothing replaces it:
        not readonly by its declared type (the root is marked written,
        `finalize_pointer_tuples`), and bound by nothing in the body nor
        through `nonlocal` in a nested def (an `Own` parameter's rebind
        writes in place under the pointers). Any other root -- a local, a
        global, a binder, a captured name -- may end, or be replaced, while
        a holder still points into it (TODO.md "Storage placement from
        loans"): the phrase naming it and why."""
        fn = self.func.name
        receiver = (name == "self" and self.func.is_method
                    and not self.func.is_staticmethod)
        if name == holder:
            return f"'{name}' itself"
        if name in self.bound:
            return (f"'{name}', a parameter '{fn}' rebinds"
                    if name in self.params or receiver
                    else f"'{name}', a local of '{fn}'")
        if (name in self.params or receiver) and name in self.nested_rebinds:
            return f"'{name}', a parameter a nested function rebinds"
        if name in self.params:
            return (f"'{name}', a readonly parameter"
                    if _readonly_type(self.params[name]) else None)
        if receiver:
            return (f"'{name}', the receiver of a readonly method"
                    if self.func.is_readonly else None)
        if name in self.captures:
            return f"'{name}', a name '{fn}' captures"
        return f"'{name}', a module global"

    def first_refused(self, ctx: 'SemanticContext', holder: str,
                      value: TpyExpr) -> str | None:
        """The phrase for the first root of a binding's value
        (`binding_roots`) in source order that `refusal` refuses; None
        when it may point into every root."""
        roots = binding_roots(ctx, value)
        if roots is None:
            return "storage with no name"
        return next((why for r in roots
                     if (why := self.refusal(holder, r)) is not None), None)


def _root_rule(func: TpyFunction, *,
               captures: 'Iterable[str]' = ()) -> _RootRule:
    rebinds: set[str] = set()
    walk_body_stmts(func.body, lambda e: None,
                    lambda s: rebinds.update(s.nonlocal_names)
                    if isinstance(s, TpyNestedDef) else None)
    return _RootRule(func, frozenset(scope_bound_names(func.body)),
                     dict(func.params), frozenset(rebinds),
                     frozenset(captures))


def _readonly_type(t: object) -> bool:
    return isinstance(t, TpyType) and isinstance(
        unwrap_send_sync(unwrap_ref_type(t)), ReadonlyType)


class _RootRefusal(NamedTuple):
    """A binding owning nothing of `holder`, a pointer tuple of the set or
    one bound from it, whose root `_RootRule.refusal` refuses: the member
    it is bound to (None: an owning tuple's own binding) and the phrase
    naming the root."""
    binding: 'TpyVarDecl | TpyAssign'
    holder: str
    member: str | None
    root: str


class _Closure(NamedTuple):
    """A body's pointer tuples (`_alias_closure`), the roots their
    bindings that own nothing point into, the bindings whose roots
    `_RootRule` refuses, and each name the set leaves out for another
    reason with its binding from the set and the member it reads there."""
    names: 'dict[str, PointerTupleOwner]'
    roots: frozenset[str]
    refused: tuple[_RootRefusal, ...]
    mixed: 'tuple[tuple[TpyVarDecl | TpyAssign, str, str], ...]' = ()


def _alias_closure(ctx: 'SemanticContext', func: TpyFunction,
                   seeds: 'dict[str, PointerTupleOwner]',
                   excluded: 'Iterable[str]', *,
                   captures: 'Iterable[str]' = ()) -> _Closure:
    """`seeds` (pointer tuple -> owner) plus every name that owns no
    element at any of its bindings and reads a seed's name, or another
    such name's, at one of them (`q = p; q = h.pair`). Each binding's
    form (`_binding_form`) decides: one that POINTS, of a seed or of such
    a name, points only into roots `_RootRule` admits; one that does not
    keeps its name out, or for a seed is refused. The greatest such set,
    so aliases bound from one another round a loop stay in it."""
    excluded = set(excluded)
    table = ctx.tuple_bindings_by_func[func]
    rule = _root_rule(func, captures=captures)

    reads: dict[str, list[str]] = {}
    alias_roots: dict[str, frozenset[str]] = {}
    # Names out of the set for an element of their own (`owning`) or for
    # bindings whose roots the rule refuses (`root_failed`).
    owning: set[str] = set()
    root_failed: dict[str, list[tuple['TpyVarDecl | TpyAssign', str]]] = {}
    for n, bindings in table.items():
        if not bindings or n in seeds or n in excluded:
            continue
        srcs: list[str] = []
        roots: set[str] = set()
        failed: list[tuple['TpyVarDecl | TpyAssign', str]] = []
        for b in bindings:
            form = _binding_form(ctx, b, None)
            if form is _Form.OWNS:
                owning.add(n)
                break
            value = binding_value(b)
            assert value is not None
            if form is _Form.READS:
                srcs.extend(_alias_sources(value) or ())
                continue
            why = rule.first_refused(ctx, n, value)
            if why is not None:
                failed.append((b, why))
            else:
                roots.update(binding_roots(ctx, value) or ())
        else:
            if failed:
                root_failed[n] = failed
            else:
                reads[n] = srcs
                alias_roots[n] = frozenset(roots)
    users: dict[str, list[str]] = {}
    for n, srcs in reads.items():
        for s in srcs:
            users.setdefault(s, []).append(n)
    alive = set(reads)
    while True:
        out = dict(seeds)
        queue = list(seeds)
        while queue:
            s = queue.pop(0)
            for u in users.get(s, ()):
                if u in alive and u not in out:
                    out[u] = out[s]
                    queue.append(u)
        # A name read only from names outside the set holds nothing the
        # set's pointers keep, so an alias reading it is no member either.
        bad = {n for n in out if n in reads
               and any(s not in out for s in reads[n])}
        if not bad:
            break
        alive -= bad
    roots = frozenset(r for n in out if n in alias_roots
                      for r in alias_roots[n])
    refused: list[_RootRefusal] = []
    for n in seeds:
        if n in excluded:
            continue
        for b in table.get(n) or ():
            if _binding_form(ctx, b, out) is not _Form.POINTS:
                continue
            value = binding_value(b)
            assert value is not None
            why = rule.first_refused(ctx, n, value)
            if why is not None:
                refused.append(_RootRefusal(b, n, None, why))
            else:
                roots |= set(binding_roots(ctx, value) or ())
    mixed: list[tuple['TpyVarDecl | TpyAssign', str, str]] = []
    for n, bindings in table.items():
        if not bindings or n in out:
            continue
        sources = [(b, _alias_sources(binding_value(b)) or [])
                   for b in bindings]
        member = next((x for _, s in sources for x in s if x in out), None)
        if member is None:
            continue
        # A name read from outside the set at an alias binding points into
        # that name's storage: a root like any other, unless the name owns
        # an element (a copy) or the rule admits it (a tuple parameter).
        failures = list(root_failed.get(n, ()))
        other = n not in reads and n not in root_failed
        if not other:
            for b, s in sources:
                if not s or _binding_form(ctx, b, out) is not _Form.POINTS:
                    continue
                x = next(x for x in s if x not in out)
                why = None if x in owning else rule.refusal(n, x)
                if why is None:
                    other = True
                    break
                failures.append((b, why))
        if other:
            first = next(b for b, s in sources if member in s)
            mixed.append((first, n, member))
        else:
            refused.extend(_RootRefusal(b, n, member, why)
                           for b, why in failures)
    return _Closure(out, roots, tuple(refused), tuple(mixed))


class PointerTupleOwner(NamedTuple):
    """Whose element storage a pointer tuple holds: the owning pointer
    tuple's name and the function that storage belongs to."""
    name: str
    home: str


def _owners(ctx: 'SemanticContext', func: TpyFunction,
            reassigned: 'Iterable[str]', excluded: 'Iterable[str]'
            ) -> 'dict[str, PointerTupleOwner]':
    tuple_name_bindings(ctx, func, "")
    return {n: PointerTupleOwner(n, func.name)
            for n, b in ctx.tuple_bindings_by_func[func].items()
            if b and rebinds_owning_tuple(ctx, func, n, reassigned,
                                          excluded=excluded)}


def pointer_tuple_names(ctx: 'SemanticContext', func: 'TpyFunction | None',
                        reassigned: 'Iterable[str]',
                        excluded: 'Iterable[str]'
                        ) -> 'Mapping[str, PointerTupleOwner]':
    """THE pointer tuples of a plain function body, each mapped to the
    owner whose element storage it holds: every name `rebinds_owning_tuple`
    decides (itself), and every name that owns no element at any of its
    bindings and is bound from one of them at one (`q = p; q = h.pair`,
    `_alias_closure`): it holds their element pointers or other storage's,
    never an object of its own -- in a nested def, the enclosing
    body's pointer tuples it captures included. Sema's copy warning, root
    marking and capture check and the binding planner read this one
    answer. Once the outermost body is analyzed (`finalize_pointer_tuples`)
    it is cached; asked during the walk it is the answer over the bindings
    stamped so far, which only grows."""
    if func is None or func.is_generator or func.is_async:
        return {}
    cached = ctx.pointer_tuples_by_func.get(func)
    if cached is not None:
        return cached
    tuple_name_bindings(ctx, func, "")
    return _alias_closure(ctx, func,
                          _owners(ctx, func, reassigned, excluded),
                          excluded).names


def final_pointer_tuple_names(ctx: 'SemanticContext',
                              func: 'TpyFunction | None'
                              ) -> 'Mapping[str, PointerTupleOwner]':
    """`pointer_tuple_names` once `finalize_pointer_tuples` fixed it, for a
    reader after sema: a body it never reached is an internal error, never
    a second computation over other inputs."""
    if func is None:
        return {}
    final = ctx.pointer_tuples_by_func.get(func)
    if final is None:
        raise AssertionError(
            f"no final pointer-tuple set for '{func.name}'")
    return final


def pointer_tuple_owned(ctx: 'SemanticContext', func: TpyFunction,
                        name: str) -> frozenset[int]:
    """The elements some binding of a pointer tuple owns."""
    return frozenset(i for b in tuple_name_bindings(ctx, func, name) or ()
                     for i, own in enumerate(ctx.tuple_elem_owned.get(b) or ())
                     if own)


def finalize_pointer_tuples(ctx: 'SemanticContext',
                            func: 'TpyFunction | None', *,
                            nested: bool,
                            compat: 'TypeCompatibility') -> None:
    """The pointer-tuple facts of `func` once its body is analyzed, the same
    whatever the order of its bindings and closures: the roots its own
    pointer tuples' bindings that own nothing point into
    (`_Closure.roots`) marked mutated (a binding takes the address of what
    it reads, `q = h.pair` of `h`'s storage); for the outermost body, the
    final sets of it, its nested defs and generator expressions, the
    declared-slot checks of the rebinds the final set leaves out
    (`TypeCompatibility.park_repointed_tuple_check`), and the closure
    rejects."""
    if func is None:
        return
    f = ctx.func
    excluded = frozenset(f.current_param_names | f.current_nonlocal_names)
    owners = _owners(ctx, func, f.current_reassigned_vars, excluded)
    ctx.pointer_tuple_seeds[func] = (owners, excluded)
    closure = (_alias_closure(ctx, func, owners, excluded)
               if not (func.is_generator or func.is_async)
               else _Closure({}, frozenset(), ()))
    final = closure.names
    for root in sorted(closure.roots):
        ctx.mark_param_mutated(root)
    if nested:
        return
    ctx.pointer_tuples_by_func[func] = final
    sets: list[tuple[TpyFunction, _Closure]] = [(func, closure)]
    defs: list[TpyNestedDef] = []
    walk_body_stmts(func.body, lambda e: None,
                    lambda s: defs.append(s)
                    if isinstance(s, TpyNestedDef) else None)
    # Each nested def and generator expression inherits the pointer tuples
    # of its creator it reads: a def those it captures (its `nonlocal`
    # names among them), a generator expression those of its captures that
    # read the creator's own binding. Defs come first, and a generator
    # expression after the one it is created in.
    inner: list[tuple[TpyFunction, TpyFunction, 'Iterable[str]']] = []
    captures = IdentityMap((nd.func, nd.captured_names) for nd in defs)
    for nd in defs:
        reads: 'Iterable[str]' = ()
        if not (nd.func.is_generator or nd.func.is_async):
            tuple_name_bindings(ctx, nd.func, "")
            reads = nd.captured_names
        inner.append((nd.func, func, reads))
    inner += [(g.func, g.creator, g.reads_creator)
              for g in ctx.genexpr_creators.pop(func, ())]
    by_func = IdentityMap((fn, c.names) for fn, c in sets)
    for fn, creator, reads in inner:
        src = by_func.get(creator) or {}
        inherited = {n: src[n] for n in reads if n in src}
        if fn.is_genexpr or fn.is_generator or fn.is_async:
            inner_closure = _Closure(inherited if fn.is_genexpr else {},
                                     frozenset(), ())
        else:
            seeds, inner_excluded = ctx.pointer_tuple_seeds.get(
                fn, ({}, frozenset()))
            inner_closure = _alias_closure(
                ctx, fn, {**inherited, **seeds}, inner_excluded,
                captures=captures.get(fn) or ())
        by_func[fn] = ctx.pointer_tuples_by_func[fn] = inner_closure.names
        if not fn.is_genexpr:
            sets.append((fn, inner_closure))
    for fn, c in sets:
        for rebind in ctx.pending_pointer_tuple_rebinds.pop(fn, ()):
            if rebind.name not in c.names:
                compat.run_repointed_tuple_check(rebind)
    _refuse_nonlocal_rebinds(ctx, func, final, defs)
    for _, c in sets:
        _refuse_mixed_bindings(ctx, c)
    _refuse_outliving_captures(ctx, [(fn, c.names) for fn, c in sets], defs)


def _keeps(name: str, owner: PointerTupleOwner, *,
           itself: bool = True) -> str:
    """What a pointer tuple holds, as the predicate of a clause about it."""
    home = f"'{owner.home}' itself" if itself else f"'{owner.home}'"
    return (f"keeps objects of its own in storage of {home}"
            if owner.name == name
            else f"may hold the objects '{owner.name}' keeps in storage "
                 f"of {home}")


def _holds(name: str, owner: PointerTupleOwner, *,
           itself: bool = True) -> str:
    return f"which {_keeps(name, owner, itself=itself)}"


def _refuse_nonlocal_rebinds(ctx: 'SemanticContext', func: TpyFunction,
                             names: 'Mapping[str, PointerTupleOwner]',
                             defs: 'list[TpyNestedDef]') -> None:
    """A nested def rebinding a pointer tuple or one of its aliases through
    `nonlocal` writes the enclosing body's element pointers where its
    alias-rebind pass never looks, so a later in-place rebind there
    clobbers what the closure re-pointed at: a located error at the
    closure's binding, which counts as a binding of the enclosing name."""
    for nd in defs:
        for n in sorted(set(nd.nonlocal_names) & set(names)):
            found: list[TpyStmt] = []
            walk_body_stmts(
                nd.func.body, lambda e: None,
                lambda s: found.append(s)
                if n in bound_names_of(s) | walrus_names_of(s) else None)
            if found:
                ctx.emit_error(
                    f"'{n}' is rebound here through 'nonlocal', and it "
                    f"{_keeps(n, names[n])}; "
                    f"rebinding it from a nested function is not yet "
                    f"supported here; rebind it in '{func.name}' itself",
                    found[0])


def _root_refusal_message(r: _RootRefusal,
                          names: 'Mapping[str, PointerTupleOwner]') -> str:
    if r.member is None:
        head = (f"'{r.holder}' "
                f"{_keeps(r.holder, names[r.holder], itself=False)}, "
                f"so a binding of it")
    else:
        head = (f"'{r.holder}' is bound to '{r.member}', "
                f"{_holds(r.member, names[r.member], itself=False)}, "
                f"so a binding of '{r.holder}'")
    return (f"{head} that owns nothing may point only into a parameter's "
            f"or self's storage for now; here it points into {r.root}; "
            f"bind this value to a name of its own")


def _refuse_mixed_bindings(ctx: 'SemanticContext',
                           closure: _Closure) -> None:
    """Every binding owning nothing of a pointer tuple, or of a name bound
    from one, whose root the root rule refuses (`_RootRule.refusal`: a
    binding owning nothing points only into a parameter's or `self`'s
    storage) is a located error naming the root. A name bound from a
    pointer tuple that the pointer tuples leave out for another reason --
    a parameter or a `nonlocal` name, or one another binding of which owns
    an element -- has no one form that holds both: a located error at the
    binding that reads the pointer tuple."""
    names = closure.names
    errors: list[tuple[str, 'TpyVarDecl | TpyAssign']] = [
        (_root_refusal_message(r, names), r.binding)
        for r in closure.refused]
    errors += [(f"'{n}' is bound here to '{src}', {_holds(src, names[src])}, "
                f"and to other storage at another of its bindings; one name "
                f"holding both is not yet supported here; bind this value to "
                f"a name of its own", b)
               for b, n, src in closure.mixed]
    for msg, b in sorted(errors, key=lambda e: (e[1].loc.line, e[1].loc.column)
                         if e[1].loc is not None else (0, 0)):
        ctx.emit_error(msg, b)


def _refuse_outliving_captures(
        ctx: 'SemanticContext',
        sets: 'list[tuple[TpyFunction, dict[str, PointerTupleOwner]]]',
        defs: 'list[TpyNestedDef]') -> None:
    """A closure that can outlive the body whose pointer tuple it captures
    would keep pointers into that body's own storage
    (BUGS.md#escaping-closure-pointer-local-capture): a nested def that
    escapes, and any lambda that copies its captures (a `Callable` value;
    no fact says whether one escapes, so every such lambda counts) at any
    depth. One located error per closure, after the body, so the order of
    the closure and the rebinds does not matter."""

    def refuse(node: 'TpyLambda | TpyNestedDef', name: str,
               owner: PointerTupleOwner) -> None:
        ctx.emit_error(
            f"a closure that outlives '{owner.home}' captures '{name}', "
            f"{_holds(name, owner)}; capturing it is not yet supported "
            f"here; bind copies of its elements "
            f"(`{name}0 = copy({name}[0])`, with `copy` from `tpy`) and "
            f"capture those", node)

    for fn, names in sets:
        refused: set[int] = set()
        for lam, chain in value_capture_lambdas(fn.body, nested=True):
            if any(id(c) in refused for c in chain):
                continue
            hit = sorted(set(lam.body_captures) & set(names))
            if hit:
                refused.add(id(lam))
                refuse(lam, hit[0], names[hit[0]])
    top = sets[0][1]
    for nd in defs:
        hit = sorted(set(nd.captured_names) & set(top))
        if nd.escapes and hit:
            refuse(nd, hit[0], top[hit[0]])


def bind_kind_of(ctx: 'SemanticContext', value: TpyExpr | None,
                 owned: tuple[bool, ...] | None = None) -> BindKind | None:
    """What a binding puts in the name's storage; None for a bare
    declaration without an initializer. A tuple binding (`owned`, its
    `tuple_elem_owned`) puts an object of its own there exactly when one of
    its elements is one; otherwise every element borrows."""
    if value is None:
        return None
    if isinstance(_peel(value), TpyNoneLiteral):
        return BindKind.NONE
    if owned is not None:
        return BindKind.RVALUE if any(owned) else BindKind.LVALUE
    return (BindKind.RVALUE if binds_owned_value(ctx, value)
            else BindKind.LVALUE)


def is_reference_local(var_type: TpyType | None,
                       owned: tuple[bool, ...] | None = None) -> bool:
    """A local whose storage a rebind can overwrite under a live alias: a
    reference type, or a tuple binding that holds a reference element of
    its own (`owned`). Value types are copied at the rebind; str/bytes have
    their own pinned-view tier."""
    if var_type is None:
        return False
    inner = unwrap_readonly(var_type)
    if isinstance(unwrap_readonly(unwrap_ref_type(unwrap_own(inner))),
                  TupleType):
        return owned is not None and any(owned)
    return not inner.is_value_type() and view_family_for_type(inner) is None


def stamp_bind_kind(ctx: 'SemanticContext', stmt: 'TpyVarDecl | TpyAssign',
                    name: str, value: TpyExpr | None,
                    var_type: TpyType | None, *, rebind: bool) -> None:
    """Record what the binding writes, and mark an rvalue REBIND of a
    reference local as a site the storage pass decides. The default is OWN
    -- the pass proves IN_PLACE."""
    owned = tuple_elem_owned(ctx, value, var_type)
    if owned is not None:
        ctx.tuple_elem_owned[stmt] = owned
    kind = bind_kind_of(ctx, value, owned)
    ctx.func.bind_kinds[stmt] = kind
    if (rebind and kind is BindKind.RVALUE
            and is_reference_local(var_type, owned)):
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
        # Per tuple site, the elements of the name's current tuple its new
        # value keeps (`kept_elems`), joined over every pass reaching it.
        self.site_kept: IdentityMap = IdentityMap()
        self.sites: list[TpyVarDecl | TpyAssign] = []
        # The names some binding of which holds a tuple element of its own.
        self.tuple_names: set[str] = set()
        self.loops: list[_Loop] = []
        self.try_sinks: list[list[_State]] = []
        self.bound_here: set[str] = set()
        # The frame-holding locals, and the ones a generator or async body
        # still holds at each loop's back edge (`loop_frames`).
        self.frames: frozenset[str] = (
            frozenset() if ctx.is_top_level
            else frozenset(ctx.func.frame_local_roots))
        fn = ctx.func.current_function
        self.frame_body = (not ctx.is_top_level and isinstance(fn, TpyFunction)
                           and (fn.is_generator or fn.is_async))
        self.loop_holds: IdentityMap = IdentityMap()
        self.bound_any: set[str] = set()
        # Every name some binding of the body points at storage it does not
        # own (FOREIGN); the rest of `bound_any` only ever owns its object.
        self.foreign_any: set[str] = set()
        # Frame-holding locals bound in a `with` body and still open when
        # its managers exit.
        self.with_holds: list[tuple[TpyWith, set[str]]] = []
        # Each for-each's variables with the root of what it iterates: a
        # write through the variable is a write into that storage.
        self.for_edges: list[tuple[set[str], str | None]] = []
        self._edges: StorageEdges | None = None
        # A plain function's frame-holding local kept in its loop body's C++
        # block is gone at the next pass.
        self.pass_scoped: IdentityMap = IdentityMap()
        for name, loop in ctx.func.pass_scoped_frames.items():
            self.pass_scoped.setdefault(loop, set()).add(name)

    def edges(self) -> StorageEdges:
        """The body's loan graph, once the walk has filed every loan."""
        if self._edges is None:
            self._edges = storage_edges(self.stmt_loans, self.for_edges)
        return self._edges

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
            if self.tuple_names:
                self.note_tuple_reads(s, st)
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
            src = _peel(s.value)
            for i, name in enumerate(s.targets):
                if name is not None:
                    # Each target holds ONE element (its loan carries the
                    # index), not the whole tuple.
                    self.bind_foreign(s, name, s.value, st, whole=False)
                    # A target that does not take the element over aliases
                    # the source's element, whether or not the tracker
                    # filed the loan (a module-level target is a global).
                    if (isinstance(src, TpyName) and src.name != name
                            and not (i < len(s.is_owned) and s.is_owned[i])):
                        st.loans[name] = st.loans[name] | {_Loan(
                            src.name, BorrowKind.ALIAS,
                            self.origins_of(src.name, st), i)}
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
            out = self.walk_stmts(s.body, st)
            if self.frame_body and out is not None:
                bound = _names_bound_in(s.body)
                held = {h for h, loans in out.loans.items()
                        if loans and h in self.frames and h in bound}
                if held:
                    self.with_holds.append((s, held))
            return out
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
            for gone in self.pass_scoped.get(s, ()):
                for pass_end in (out, *loop.continues, *loop.breaks):
                    if pass_end is not None:
                        pass_end.loans.pop(gone, None)
                        pass_end.origins.pop(gone, None)
            new_head = _join_all([st, out, *loop.continues])
            assert new_head is not None
            if (new_head.origins == head.origins
                    and new_head.loans == head.loans):
                if self.frame_body:
                    self.note_loop_holds(s, _join_all([out, *loop.continues]))
                return head, loop
            head = new_head
        raise AssertionError("alias-rebind loop replay did not converge")

    def walk_for(self, s: TpyForEach, st: _State) -> _State | None:
        self.for_edges.append((bound_names_of(s),
                               _root_name_of_expr(_peel(s.iterable))))
        self.expr_effects(s.iterable, st, s, top_is_bind=True)
        groups = self.groups(s)
        iter_key = f"{ITER_BORROWER}#{self.id_of(s)}"
        iter_loans = self.loans_of(iter_key, groups, None, st) | frozenset(
            _Loan(root, BorrowKind.OPAQUE, self.origins_of(root, st))
            for root in _frame_roots(_peel(s.iterable)))
        if iter_loans:
            st.loans[iter_key] = iter_loans

        # The iterable is evaluated once, before the first pass: what the
        # variable borrows is the storage its source held then, never what a
        # rebind in the body has put there since.
        var_loans = self.loans_of(s.var, groups, s.iterable, st)

        def at_head(state: _State) -> None:
            self.bind_foreign(s, s.var, s.iterable, state)
            state.loans[s.var] = var_loans

        head, loop = self.walk_loop_body(s, st, at_head)
        exit_st = head.copy()
        exit_st.loans.pop(iter_key, None)
        for b in loop.breaks:
            b.loans.pop(iter_key, None)
        out = self.walk_stmts(s.orelse, exit_st)
        return _join_all([out, *loop.breaks])

    def walk_while(self, s: TpyWhile, st: _State) -> _State | None:
        def at_head(state: _State) -> None:
            # The condition runs again after every pass: its reads see what
            # the back edge brings in, not only the loop's entry.
            if self.tuple_names:
                self.note_expr_reads([s.condition], state)
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
                self.bound_any.add(holder)
                self.foreign_any.add(holder)
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
                self.bound_any.add(h.binding)
                self.foreign_any.add(h.binding)
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

    def groups(self, stmt: TpyStmt) -> dict[str, list[tuple[str, LoanInfo]]]:
        out: dict[str, list[tuple[str, LoanInfo]]] = {}
        for storage, holder, info in self.stmt_loans.get(stmt, ()):
            out.setdefault(holder, []).append((storage, info))
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
                and is_reference_local(self.local_type(name, value),
                                       self.ctx.tuple_elem_owned.get(node))):
            site = True
        if site:
            prev = self.site_states.get(stmt)
            if prev is None:
                self.sites.append(stmt)  # type: ignore[arg-type]
            self.site_states[stmt] = _join(prev, st)
            if value is not None:
                self.site_kept[stmt] = (self.site_kept.get(stmt, frozenset())
                                        | kept_elems(name, value))
        st.loans[name] = self.loans_of(name, self.groups(stmt), value, st)
        if any(self.ctx.tuple_elem_owned.get(node) or ()):
            self.tuple_names.add(name)
        if kind is BindKind.RVALUE and name not in self.always_foreign:
            st.origins[name] = frozenset((self.id_of(node),))
        else:
            st.origins[name] = frozenset((FOREIGN,))
            self.foreign_any.add(name)
        self.bound_here.add(name)
        self.bound_any.add(name)

    def bind_foreign(self, stmt: TpyStmt, name: str, source: TpyExpr | None,
                     st: _State, *, whole: bool = True) -> None:
        st.loans[name] = self.loans_of(name, self.groups(stmt), source, st,
                                       whole=whole)
        st.origins[name] = frozenset((FOREIGN,))
        self.bound_here.add(name)
        self.bound_any.add(name)
        self.foreign_any.add(name)

    def bind_other_holders(self, stmt: TpyStmt, st: _State) -> None:
        """Loans registered on a statement for a holder the statement's own
        shape did not bind (a capture, a target bound by a helper)."""
        for holder in self.groups(stmt):
            if holder not in self.bound_here and holder != ITER_BORROWER:
                self.bind_foreign(stmt, holder, None, st)

    def loans_of(self, holder: str, groups: dict[str, list[tuple[str, LoanInfo]]],
                 value: TpyExpr | None, st: _State, *,
                 whole: bool = True) -> frozenset[_Loan]:
        """`whole`: the holder binds the value itself (`u = p`), so a tuple
        value lends it every owned element; an unpack target binds one
        element and carries only that element's loan."""
        out: set[_Loan] = set()
        for root, info in groups.get(holder, ()):
            index = info.elem_index
            # A loan held through a borrowed element points at storage
            # outside the tuple, which the tuple's rebind does not touch.
            if info.through_pointer:
                continue
            out.add(_Loan(root, info.kind, self.origins_of(root, st),
                          index[1] if index is not None and index[0] == "lit"
                          and isinstance(index[1], int) else None))
        if value is None:
            return frozenset(out)
        inner = _peel(value)
        # A holder bound from another holder carries that holder's loans:
        # a copied Ptr, an alias of an alias, a view read off a pointer.
        if not carries_no_borrow(self.ctx, value):
            for n in read_names(inner):
                if n in st.loans:
                    out |= st.loans[n]
        # A whole tuple alias (`u = p`) holds what `p` holds, so it is a loan
        # on every element `p` owns, as `a = p[i]` is on element `i`.
        for arm in (((inner.then_expr, inner.else_expr)
                     if isinstance(inner, TpyIfExpr) else (inner,))
                    if whole else ()):
            arm = _peel(arm)
            if isinstance(arm, TpyName) and arm.name != holder:
                origins = self.origins_of(arm.name, st)
                for i in self.owned_at(self.local_type(arm.name, arm),
                                       origins):
                    out.add(_Loan(arm.name, BorrowKind.ALIAS, origins, i))
        # A generator object keeps what its frame borrows for as long as it
        # lives, and any call result keeps what its callee's recorded return
        # borrows name (`c = mk(g)` where `mk` returns a coroutine over it).
        for root in _frame_roots(inner) + self.result_borrow_roots(inner):
            if root != holder:
                out.add(_Loan(root, BorrowKind.OPAQUE,
                              self.origins_of(root, st)))
        if holder in self.frames:
            # What sema traced the frame to (a loop variable over a
            # container read as the container) -- the storage a rebind of
            # that name would pull out from under it.
            fact = self.ctx.func.frame_local_roots.get(holder)
            for root in fact[0] if fact is not None else ():
                if root is not None and root != holder:
                    out.add(_Loan(root, BorrowKind.OPAQUE,
                                  self.origins_of(root, st)))
        return frozenset(out)

    def note_tuple_reads(self, s: TpyStmt, st: _State) -> None:
        """Record, at each last-use read of a tuple local in `s`, the
        elements every storage reaching it owns -- joined over every pass
        that reaches the read."""
        self.note_expr_reads(s.exprs(), st)

    def note_expr_reads(self, exprs: 'Iterable[TpyExpr]', st: _State) -> None:
        uses = self.ctx.tuple_owned_at_use
        for e in exprs:
            stack = [e]
            while stack:
                n = stack.pop()
                stack.extend(n.children())
                if not (isinstance(n, TpyName) and n.name in self.tuple_names
                        and self.ctx.is_last_use(n)):
                    continue
                origins = st.origins.get(n.name) or frozenset((FOREIGN,))
                masks = [self.ctx.tuple_elem_owned.get(self.by_id[o]) or ()
                         if o >= 0 else () for o in origins]
                width = max((len(m) for m in masks), default=0)
                owned = tuple(all(i < len(m) and m[i] for m in masks)
                              for i in range(width))
                prev = uses.get(n)
                uses[n] = (owned if prev is None else tuple(
                    a and b for a, b in zip(prev, owned)))

    def owned_at(self, slot: TpyType | None,
                 origins: frozenset[int]) -> set[int]:
        """The elements a tuple of type `slot` may hold as objects of its own
        when its storage is one of `origins`: each binding's
        `tuple_elem_owned`, and every reference element of storage no binding
        of this body made. Empty for a slot that is no tuple holding a
        reference element."""
        ref = _reference_elements(slot)
        if ref is None:
            return set()
        out: set[int] = set()
        for o in origins:
            mask = (ref if o < 0
                    else self.ctx.tuple_elem_owned.get(self.by_id[o]) or ())
            out.update(i for i, own in enumerate(mask) if own)
        return out

    def result_borrow_roots(self, e: TpyExpr) -> list[str]:
        """What a call's result keeps per its callee's recorded return
        borrows."""
        return [root for src in self.result_lend_exprs(e)
                for root in _borrow_storage_roots(src)]

    def result_lend_exprs(self, e: TpyExpr) -> list[TpyExpr]:
        """The operands a call's result may point into."""
        ops = call_borrow_operands(e)
        if ops is None:
            return []
        # Module statements are analyzed before any body, so a module
        # function's borrow facts are still unknown here; unknown means
        # aliased -- every lent argument -- as the pass's rule says
        # (BUGS.md#module-level-call-source-facts-not-ready).
        sources = (None if self.ctx.is_top_level
                   and ops.fi.root.return_borrows_from is None
                   else recorded_return_borrow_sources(ops.fi))
        return [src.expr for src in call_lend_sources(ops, sources,
                                                      expr_type=None)]

    def origins_of(self, root: str, st: _State) -> frozenset[int]:
        base = root.split(".", 1)[0]
        return st.origins.get(base, frozenset((FOREIGN,)))

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
                    _Loan(root, BorrowKind.PTR, self.origins_of(root, st))}
        # A generator or coroutine handed over to an owning parameter
        # (`create_task(consume(g))`) outlives the statement, and so do its
        # loans; one lent to a borrowing parameter (`list(relay(g))`) dies
        # with the statement.
        for arg in _owned_args(inner):
            arg = _peel(arg)
            roots = _frame_roots(arg) + self.result_borrow_roots(arg)
            if isinstance(arg, TpyName) and arg.name in self.frames:
                # The generator or coroutine object itself changes hands.
                roots.append(arg.name)
            for root in roots:
                st.loans[_ESCAPED] = st.loans.get(_ESCAPED, frozenset()) | {
                    _Loan(root, BorrowKind.OPAQUE, self.origins_of(root, st))}
        # TODO: walk through parse.nodes.walk_expr_tree (the shared pruning visitor) instead of an own children() loop.
        for child in inner.children():
            self.expr_effects(child, st, stmt)
        if isinstance(inner, TpyAwait):
            # `await c` runs the coroutine to its end: it holds nothing after.
            awaited = _peel(inner.value)
            if isinstance(awaited, TpyName) and awaited.name in self.frames:
                st.loans.pop(awaited.name, None)

    # -- frames held across a loop's back edge --

    def note_loop_holds(self, loop: TpyStmt, back: _State | None) -> None:
        """The frame-holding locals first bound in `loop`'s body that still
        hold a loan where the next pass starts."""
        if back is None:
            return
        body = loop_bindings_of(self.ctx.loop_bindings, loop,
                                self.ctx.write_summaries).body
        held = {h for h, loans in back.loans.items()
                if loans and h in self.frames and h in body}
        # One whose borrows cannot be traced holds no loan to see; it is
        # held while it is still bound.
        roots = self.ctx.func.frame_local_roots
        held |= {h for h in back.origins
                 if h in self.frames and h in body and roots.get(h) is None
                 and back.origins[h]}
        if held:
            self.loop_holds.setdefault(loop, set()).update(held)

    def check_loop_holds(self) -> None:
        """Reject a frame held across a back edge whose loop may write
        what it borrows (`loop_frames`)."""
        for loop, holders in self.loop_holds.items():
            for holder in sorted(holders):
                fact = self.ctx.func.frame_local_roots.get(holder)
                if fact is None:
                    nodes = self.ctx.func.frame_binding_nodes.get(holder)
                    raise self.ctx.error(
                        f"cannot keep '{holder}' open across passes of this "
                        f"loop: what it borrows cannot be traced; bind it "
                        f"from a direct call over named storage, close it "
                        f"with 'del {holder}' before the pass ends, or "
                        f"iterate it inside a helper function",
                        nodes[0] if nodes else loop)
                roots = {r for r in fact[0] if r is not None}
                upstream, reach = storage_closure(
                    self.edges(), self.frames, roots)
                nodes = self.ctx.func.frame_binding_nodes.get(holder) or [loop]
                calls = [frame_binding_calls(binding_value(n)) for n in nodes]
                own_calls = tuple(c for cs in calls for c in cs or ())
                check_loop_hold(self.ctx, LoopFrameHold(
                    holder=holder, loop=loop,
                    upstream=frozenset(upstream), reach=frozenset(reach),
                    globals_=frozenset(n for n in upstream
                                       if self.is_module_global(n)),
                    node=nodes[0], own_calls=own_calls))

    def frame_closure(self, holder: str
                      ) -> tuple[frozenset[str], frozenset[str]]:
        """The storage a frame-holding local may see written (reach) and
        the module globals among what it borrows."""
        fact = self.ctx.func.frame_local_roots.get(holder)
        roots = ({r for r in fact[0] if r is not None}
                 if fact is not None else
                 {r.split(".", 1)[0] for r, *_ in self.hold_loans(holder)})
        upstream, reach = storage_closure(self.edges(), self.frames, roots)
        return (frozenset(reach),
                frozenset(n for n in upstream if self.is_module_global(n)))

    def check_with_holds(self) -> None:
        """Queue each frame kept open past a `with` block for the check of
        what the block's exit may write: the exit runs while it is open."""
        for stmt, holders in self.with_holds:
            for holder in sorted(holders):
                reach, globals_ = self.frame_closure(holder)
                nodes = self.ctx.func.frame_binding_nodes.get(holder)
                self.ctx.pending_with_exit_checks.append(WithExitCheck(
                    stmt=stmt, holder=holder, reach=reach, globals_=globals_,
                    node=nodes[0] if nodes else stmt))

    def hold_loans(self, holder: str) -> frozenset[_Loan]:
        return frozenset(
            _Loan(storage, info.kind, frozenset())
            for entries in self.stmt_loans.values()
            for storage, h, info in entries if h == holder)

    def is_module_global(self, name: str) -> bool:
        """`name` reads a module global here: declared `global`, or bound
        at module level and nowhere in this body."""
        if name in self.ctx.func.global_declarations:
            return True
        return (name in self.ctx.global_scope.bindings
                and name not in self.bound_any
                and name not in self.ctx.func.current_param_names)

    # -- the decision --

    def decide(self) -> None:
        """Set IN_PLACE on every stamped site the replay proves, and warn
        on the residual clobber at every site. The two are separate
        questions: a site some path reaches with storage it does not own
        stays OWN, yet its own slot is still reused when it runs again,
        and a holder that still reads the previous object is clobbered
        all the same.

        Every verdict is decided before any clobber is asked: an in-place
        site's object lives in the slot of the site it writes into, so
        whether a loan sits on a site's slot depends on the verdicts of
        sites the replay visits after it."""
        # Per site, the loans that may sit on its slot: (holder, kind, each
        # origin of the loan with an element it reaches -- None for a
        # scalar).
        pending: list[tuple['TpyVarDecl | TpyAssign', str, TpyType | None,
                            bool, int | None,
                            list[tuple[str, BorrowKind,
                                       list[tuple[int, int | None]]]]]] = []
        # The first error stops the compile after the warnings of the sites
        # before it, as raising at the site itself would.
        error: tuple[int, SemanticError] | None = None
        for stmt in self.sites:
            name = stmt.name if isinstance(stmt, TpyVarDecl) else stmt.target.name  # type: ignore[union-attr]
            value = stmt.init if isinstance(stmt, TpyVarDecl) else stmt.value
            st = self.site_states.get(stmt)
            var_type = self.local_type(name, value)
            frame_sites = self.ctx.func.frame_rebind_sites
            if st is None:
                continue  # unreachable: OWN
            # None: liveness never walked the body -- decide, but stay quiet.
            live = stmt.live_names_after
            origins = st.origins.get(name)
            if not origins:
                # The name holds nothing here: never bound on any path (the
                # `else` arm's first binding -- nothing an alias could see,
                # no verdict needed) or deleted (`del p`, an empty set): the
                # site keeps its OWN default, every owned element of a tuple
                # site with it, since an alias of the deleted tuple's objects
                # may still read them.
                if (origins is not None
                        and _reference_elements(var_type) is not None
                        and getattr(stmt, "rebind_storage", None)
                        is RebindStorage.OWN):
                    self.note_tuple_site(stmt, var_type, origins, None,
                                         decidable=False)
                    if not self.ctx.is_top_level:
                        self.ctx.tuple_own_sites[stmt] = None
                continue
            # A generator name that holds nothing on every path reaching
            # here (`else: h = gen(xs)` after `if c: h = gen(xs)`) is
            # bound for the first time, not rebound.
            if stmt in frame_sites and origins - {UNBOUND}:
                # Rebuilding the frame in place would need proof that nothing
                # -- an alias, a frame, a task, a view of a yielded value --
                # still reaches the old generator, and a slot of its own would
                # outlive the name's; a generator name is bound once.
                if error is None:
                    error = (len(pending), self.ctx.error(
                        f"cannot rebind '{name}' to a new generator: a name "
                        f"holding a generator is bound once, since something "
                        f"may still reach the old one; bind the new generator "
                        f"to a new name",
                        stmt))
                continue
            # A tuple a frame holds is one storage tuple, replaced whole, so
            # a path on which the name holds no tuple yet leaves nothing an
            # alias could see. Anywhere else each owned element is the
            # scalar's reference local, and an unbound path is the scalar's
            # null pointer.
            is_tuple = _reference_elements(var_type) is not None
            not_owned = (frozenset((FOREIGN,)) if is_tuple and self.frame_body
                         else _NOT_OWNED)
            decidable = (getattr(stmt, "rebind_storage", None) is not None
                         and not (origins & not_owned)
                         and not self.in_place_unrenderable(var_type, origins))
            own_by: set[str] = set()
            # The owned elements some holder's loan points into.
            hits: set[int] = set()
            clashes: list[tuple[str, BorrowKind,
                                list[tuple[int, int | None]]]] = []
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
                for root, kind, lorigins, elem in loans:
                    if root != name and not root.startswith(prefix):
                        continue
                    # The rebind replaces only what the tuple owns: a loan
                    # through a borrowed element points elsewhere.
                    common = lorigins & origins
                    if common and not is_tuple:
                        own_by.add(holder)
                    elif common:
                        owned = self.owned_at(var_type, common)
                        hit = owned if elem is None else owned & {elem}
                        if hit:
                            own_by.add(holder)
                            hits |= hit
                    # A loan on this very site's storage that is still read
                    # after the site runs again: one slot cannot hold both.
                    # Asked of every loan on the name, not only one on what
                    # it holds here: an in-place site's object lives in the
                    # slot of the site it writes into (`slots_of`). A NAMED
                    # call result holding the slot (OPAQUE) is clobbered
                    # the same way; unknown borrow facts count as aliased,
                    # so the warning may be conservative there. An immortal
                    # holder (a pointer handed out through a call, a frame
                    # consumed by a call) has no liveness to consult: it
                    # makes the site OWN but is too coarse for a warning.
                    if not (read_again and not immortal
                            and site_id is not None
                            and not (kind is BorrowKind.ALIAS
                                     and (holder, name) in escape_warned)):
                        continue
                    # Each origin's object, per element it owns there.
                    reached: list[tuple[int, int | None]] = []
                    for o in sorted(lorigins):
                        if not is_tuple:
                            reached.append((o, None))
                            continue
                        owned = self.owned_at(var_type, frozenset((o,)))
                        reached += [(o, e) for e in sorted(owned)
                                    if elem is None or e == elem]
                    if reached:
                        clashes.append((holder, kind, reached))
            kept = False
            if is_tuple:
                kept = self.note_tuple_site(stmt, var_type, origins, hits,
                                            decidable=decidable)
            elif decidable and not own_by:
                stmt.rebind_storage = RebindStorage.IN_PLACE
            if (is_tuple and not self.ctx.is_top_level
                    and getattr(stmt, "rebind_storage", None)
                    is RebindStorage.OWN):
                # A frame, an `Own` parameter and a loop variable have no
                # storage of their own for a tuple site yet; their lowering
                # refuses the site, naming the holder.
                named = sorted(h for h in own_by if h != _ESCAPED
                               and not h.startswith(ITER_BORROWER))
                # A pointer handed out of the tuple, an iterator over it, or
                # the new value itself holds the object with no name to
                # report.
                self.ctx.tuple_own_sites[stmt] = (
                    named[0] if named
                    else ESCAPED_HOLDER if own_by or kept else None)
            pending.append((stmt, name, var_type, is_tuple, site_id, clashes))
        slots: dict[tuple[int, int | None], set[int]] = {}
        for stmt, name, var_type, is_tuple, site_id, clashes in (
                pending if error is None else pending[:error[0]]):
            clobbered: dict[str, BorrowKind] = {}
            for holder, kind, reached in clashes:
                for key in reached:
                    if key not in slots:
                        slots[key] = self.slots_of(*key)
                    if site_id in slots[key]:
                        clobbered[holder] = kind
                        break
            if clobbered:
                self.warn(stmt, name, var_type, clobbered,
                          whole_object=not is_tuple)
        if error is not None:
            raise error[1]

    def slots_of(self, origin: int, elem: int | None) -> set[int]:
        """The sites whose slot may hold the object bound at `origin` (its
        element `elem`; None for a scalar): its own, or where that site
        writes in place -- it has no slot then -- every slot the name may
        hold there."""
        out: set[int] = set()
        seen: set[int] = set()
        stack = [origin]
        while stack:
            o = stack.pop()
            if o in seen:
                continue
            seen.add(o)
            node = self.by_id[o] if o >= 0 else None
            st = self.site_states.get(node) if node is not None else None
            if st is None or not self.writes_in_place(node, elem):
                out.add(o)
                continue
            name = (node.name if isinstance(node, TpyVarDecl)
                    else node.target.name)  # type: ignore[union-attr]
            stack.extend(st.origins.get(name, ()))
        return out

    def writes_in_place(self, stmt: object, elem: int | None) -> bool:
        verdict = (self.ctx.tuple_elem_storage.get(stmt)
                   if elem is not None else None)
        if verdict is not None:
            return elem < len(verdict) and verdict[elem] is RebindStorage.IN_PLACE  # type: ignore[operator]
        return getattr(stmt, "rebind_storage", None) is RebindStorage.IN_PLACE

    def note_tuple_site(self, stmt: 'TpyVarDecl | TpyAssign',
                        var_type: TpyType | None, origins: frozenset[int],
                        hits: set[int] | None, *, decidable: bool) -> bool:
        """Decide each element of a tuple site the binding owns, and the
        site from them: IN_PLACE only where nothing can observe the old
        object -- every storage the name may hold owns the element, no
        holder's loan (`hits`, None: unknown) points into it, and the new
        value does not keep it (`site_kept`). Returns whether the new value
        keeps an element it would otherwise overwrite."""
        elem_owned = self.ctx.tuple_elem_owned.get(stmt)
        if (elem_owned is None
                or getattr(stmt, "rebind_storage", None) is None):
            return False
        kept_raw = self.site_kept.get(stmt, frozenset())
        kept = ({k for k in kept_raw if k is not None}
                | (self.owned_at(var_type, origins)
                   if None in kept_raw else set()))
        masks = [self.ctx.tuple_elem_owned.get(self.by_id[o]) or ()
                 for o in origins if o >= 0]
        verdict: list[RebindStorage | None] = []
        kept_hit = False
        for i, own in enumerate(elem_owned):
            if not own:
                verdict.append(None)
                continue
            # A module function's return borrows are unknown when module
            # statements are analyzed, so a callee handing out the global
            # is a pointer no loan records: every owned element of a
            # module-level site takes storage of its own
            # (BUGS.md#module-level-call-source-facts-not-ready,
            # BUGS.md#module-rebind-callee-returned-global-clobbers).
            in_place = (not self.ctx.is_top_level
                        and decidable and hits is not None and i not in hits
                        and all(i < len(m) and m[i] for m in masks))
            if in_place and i in kept:
                kept_hit = True
                in_place = False
            verdict.append(RebindStorage.IN_PLACE if in_place
                           else RebindStorage.OWN)
        self.ctx.tuple_elem_storage[stmt] = tuple(verdict)
        if all(v is not RebindStorage.OWN for v in verdict):
            stmt.rebind_storage = RebindStorage.IN_PLACE
        return kept_hit

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
             clobbered: dict[str, BorrowKind], *, whole_object: bool) -> None:
        alias = sorted(clobbered)[0]
        if alias == name and self.site_kept.get(stmt):
            # The rebind's own value keeps part of the value it replaces
            # (`p = mixed(i, p[0])` in a loop): no second name to rebind,
            # and no rewrite keeps the carried object alive yet (a
            # `copy()` argument would be a temporary the result borrows).
            self.ctx.warning(
                f"'{name}' will not keep the object it carries over from its "
                f"previous value -- this rebind writes the new object into "
                f"that object's storage each time the loop runs it again; "
                f"keeping it is not yet supported here", stmt)
            return
        if var_type is not None and self.ctx.is_type_nocopy(var_type):
            fix = (f"both names share its storage and '{name}' cannot be "
                   f"copied; bind the new value to a name of its own")
        elif clobbered[alias] is BorrowKind.ALIAS and whole_object:
            # Not for a tuple: its copy duplicates the elements it owns,
            # where CPython's `copy` of a tuple is the same object.
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


def _owned_args(e: TpyExpr) -> list[TpyExpr]:
    """The arguments of call `e` its callee takes as `Own[...]`."""
    return [a for p, a in call_param_args(e)
            if isinstance(unwrap_readonly(p.type), OwnType)]


def _names_bound_in(body: list[TpyStmt]) -> set[str]:
    """Every name a statement list binds, nested blocks included and
    nested defs' bodies not."""
    out: set[str] = set()
    for s in body:
        out |= bound_names_of(s) | walrus_names_of(s)
        if not isinstance(s, TpyNestedDef):
            for sub in s.sub_bodies():
                out |= _names_bound_in(sub)
    return out


def _frame_roots(e: TpyExpr) -> list[str]:
    operands = frame_borrowed_operands(e)
    return [root for src in operands or ()
            for root in _borrow_storage_roots(src)]


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
    resolve_source_writes(
        ctx, replay.bound_any - replay.foreign_any - set(always_foreign)
        - set(initial_foreign), replay.edges(), replay.frames)
    replay.decide()
    replay.check_loop_holds()
    replay.check_with_holds()
    # The per-name verdict the frame layout consumes: a local with any site
    # left OWN (decided, or never reached) goes pointer-form on a frame.
    ctx.func.own_rebind_names = frozenset(
        s.name if isinstance(s, TpyVarDecl) else s.target.name  # type: ignore[union-attr]
        for s in replay.gate_sites
        if s.rebind_storage is RebindStorage.OWN)
