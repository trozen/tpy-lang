"""Alias-rebind storage pass.

Rebinding a reference-typed local to a fresh object (`p = Point(2)`) gives
the site storage of its OWN, so an alias that still points at the old
object keeps it: the old object lives until its slot dies or the site runs
again. Writing the new object INTO the name's current storage would destroy
the old one at the rebind, as CPython's refcount drop does, but needs proof
that nothing reaches it -- a constructor or a setter may keep a pointer
anywhere -- and that proof is MIR's to give. Each owned element of a tuple
(`tuple_elem_owned`) is the scalar's reference local, asked per element.

The pass replays the body in program order -- a forward dataflow over the
AST with a fixpoint at every loop -- to find the one clobber OWN storage
cannot avoid: a loan taken from a site's own slot and still READ after the
site re-executes on the next iteration. Two facts flow:

* `origins`: for each name, the statements whose write may be the one its
  storage currently holds. Every rvalue bind is its own origin; a borrow
  of someone else's storage (an lvalue bind, a parameter, a loop variable,
  a captured or global name, `None`) is FOREIGN.
* `loans`: for each holder, the loans it carries -- (root, kind, origins of
  the root at bind time, and for a tuple root the element the loan points
  into). The borrow tracker registers most of them during the walk
  (`BorrowTracker.stmt_loans`); the replay adds what the tracker does not
  model, on the side of "unknown means aliased": a holder bound by a path
  into an owned tuple element (`saved = t[0].xs[1]`) holds that element, a
  holder copied from another holder inherits its loans, a generator or
  coroutine call keeps what its frame borrows (`frame_operands`)
  and any call result what its callee's recorded return borrows name, and
  a pointer that leaves through a call argument and a frame handed to an
  `Own` parameter are held for ever.

A name holding a generator is never rebound: a rebind site of one that some
path reaches with a generator already bound is an error.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import (TYPE_CHECKING, Callable, Container, Iterable, Iterator,
                    Mapping, NamedTuple, Protocol)

from ..holders import Holder, IterHolder
from ..identity_map import IdentityMap, IdentitySet
from ..parse import (
    TpyAssign, TpyAwait, TpyBreak, TpyCall, TpyCoerce, TpyContinue, TpyDelVar,
    TpyExpr, TpyFieldAccess, TpyForEach, TpyFunction,
    TpyGlobal, TpyIf, TpyIfExpr, TpyLambda, TpyMatch,
    TpyMethodCall, TpyName, TpyNamedExpr, TpyNestedDef, TpyNoneLiteral,
    TpyRaise, TpyReturn, TpySlice, TpyStmt, TpySubscript,
    TpyTry,
    TpyTupleLiteral, TpyTupleUnpack, TpyVarDecl, TpyWhile, TpyWith,
    TupleElemCapture, iter_capture_bindings,
)
from ..parse.nodes import (RebindStorage, read_names,
                           value_capture_lambdas, walk_body_stmts,
                           walk_expr_tree)
from ..storage_trace import StepKind, Trace, is_unbound_field, trace
from ..prescan import (bound_names_of, loop_bindings_of, scope_bound_names,
                       walrus_names_of)
from ..typesys import (
    OwnType, ReadonlyType, TpyType, TupleType,
    unwrap_own, unwrap_readonly,
    unwrap_ref_type, unwrap_send_sync, view_family_for_type,
)
from ..value_category import (binds_owned_value, call_operands,
                              peel_coerce)
from .call_lends import (
    ContractLend, MayLend, borrowing_ctor_arg, frame_operand_keys,
    leaves_hold_no_pointer, may_hold_pointer,
)
from .context import (
    BorrowKind, LoanInfo, UnnamedHolder,
    ElemPath, direct_elements, frame_binding_calls,
    note_nonowning_bind,
)
from .storage_walk import (Place, StorageKey, Use, call_loans, leaves,
                           storage_roots, tuple_subscript_index)
from .loop_frames import (
    LoopFrameHold, WithExitCheck, advanced_root, binding_value,
    check_loop_hold,
    StorageEdges, resolve_source_writes, storage_closure, storage_edges,
)

if TYPE_CHECKING:
    from .compatibility import TypeCompatibility
    from .context import SemanticContext


# Origins that are not a statement of the body: storage the name does not
# own (FOREIGN), and "no binding reaches here on some path" (UNBOUND).
FOREIGN = -1
UNBOUND = -2


class _Sink(Enum):
    # Holder of every pointer that left through a call argument: never dies.
    # The replay's own; the borrow tracker never holds it.
    ESCAPED = "escaped"


_ESCAPED = _Sink.ESCAPED

# What holds loans in the replay: the tracker's holders and the sink.
_ReplayHolder = Holder | _Sink

class _Loan(NamedTuple):
    """One loan a holder carries: the root it is on, its kind, the origins
    of the root when it was taken, and -- for a tuple root -- which element
    of it the loan points into (None: the whole tuple, or an element no
    literal index names)."""
    root: StorageKey
    kind: BorrowKind
    origins: frozenset[int]
    elem: int | None = None


def _loan_on(loan_root: StorageKey, name: str) -> bool:
    """A loan whose storage key is `loan_root` is on `name`'s storage (the
    key may be a field path below its name, `t.xs`)."""
    return loan_root.root == name


def keeps_pointer(t: TpyType | None, *, fresh: bool) -> bool:
    """Whether a holder of type `t` may keep a pointer into what it is
    bound from: a fresh object only when its type may hold one
    (`may_hold_pointer`), any other holder unless every leaf of its
    type is pointer-free (`leaves_hold_no_pointer`). Unknown may."""
    if t is None:
        return True
    return may_hold_pointer(t) if fresh else not leaves_hold_no_pointer(t)


class BindKind(Enum):
    """What a name binding puts in the name's storage -- recorded by sema for
    every binding of a name (var-decl, assign-to-name, walrus) in
    `ctx.func.bind_kinds`. RVALUE: a fresh object the name owns. LVALUE: a
    borrow of some other storage. NONE: the `None` literal (no object)."""
    RVALUE = "rvalue"
    LVALUE = "lvalue"
    NONE = "none"


# -- sema-side stamps ---------------------------------------------------------

def peel_slot(t: TpyType) -> TpyType:
    """The payload a slot or a leaf of type `t` holds: its wrappers
    (`Own`, `readonly`, a reference, Send/Sync) peeled."""
    return unwrap_readonly(unwrap_ref_type(unwrap_own(unwrap_send_sync(t))))


def bare_tuple(t: TpyType | None) -> TupleType | None:
    """`t` as a tuple, its wrappers (`peel_slot`) peeled; None for no
    tuple."""
    bare = peel_slot(t) if t is not None else None
    return bare if isinstance(bare, TupleType) else None


def _leaves_at(et: TpyType, path: ElemPath
               ) -> Iterator[tuple[ElemPath, TpyType]]:
    """Every leaf of a value of type `et` at `path` with its payload type:
    the members of a nested tuple, any other type itself. The one walk
    over a tuple's leaves (`leaf_paths`, `reference_paths`)."""
    e = peel_slot(et)
    if isinstance(e, TupleType):
        for i, sub in enumerate(e.element_types):
            yield from _leaves_at(sub, path + (i,))
    else:
        yield path, e


def leaf_paths(slot: TpyType | None,
               prefix: ElemPath = ()) -> frozenset[ElemPath]:
    """Every leaf path of a value of type `slot` -- top-level `(i,)`,
    nested `(i, j)` through nested tuples, value leaves included -- and the
    one path `prefix` of a value that is no tuple."""
    if slot is None:
        return frozenset({prefix})
    return frozenset(p for p, _e in _leaves_at(slot, prefix))


def _paths_at(et: TpyType, path: ElemPath) -> frozenset[ElemPath]:
    """The reference paths of a tuple element of type `et` at `path`: its
    own when it is a reference leaf (an open type parameter included: it
    may be one), its members' when it is a nested value tuple."""
    return frozenset(p for p, e in _leaves_at(et, path)
                     if not e.is_value_type())


def reference_paths(slot: TpyType | None,
                    prefix: ElemPath = ()) -> frozenset[ElemPath]:
    """The element paths of tuple `slot` -- top-level `(i,)`, nested
    `(i, j)` through value tuples -- whose leaf is a reference: the
    reference projection of `leaf_paths`. Empty for no tuple."""
    if bare_tuple(slot) is None:
        return frozenset()
    return _paths_at(slot, prefix)


def _reference_elements(slot: TpyType | None) -> tuple[bool, ...] | None:
    """Per element of a tuple slot, whether it is a reference type; None
    when the slot is no tuple or holds no reference element at all."""
    bare = bare_tuple(slot)
    if bare is None:
        return None
    paths = reference_paths(bare)
    direct = direct_elements(paths)
    ref = tuple(i in direct for i in range(len(bare.element_types)))
    return ref if any(ref) else None


def _declared_borrowed(declared: TpyType, slot: TpyType,
                       path: ElemPath, paths_at: Callable[
                           [TpyType, ElemPath], frozenset[ElemPath]]
                       = _paths_at) -> frozenset[ElemPath]:
    """The paths at `path` a call's declared return element `declared`
    lends: an `Own[...]` marker hands its subtree over, a nested tuple
    answers per member, anything else lends every path of the slot there
    (`paths_at`: its reference paths, or every leaf path)."""
    d = unwrap_readonly(unwrap_send_sync(declared))
    if isinstance(d, OwnType):
        return frozenset()
    dt, st = bare_tuple(d), bare_tuple(slot)
    if (dt is None or st is None
            or len(dt.element_types) != len(st.element_types)):
        return paths_at(slot, path)
    out: set[ElemPath] = set()
    for i, (de, se) in enumerate(zip(dt.element_types, st.element_types)):
        out |= _declared_borrowed(de, se, path + (i,), paths_at)
    return frozenset(out)


def owned_slot_paths(slot: TpyType | None) -> frozenset[ElemPath]:
    """The element paths (`reference_paths`) of tuple `slot` its `Own`
    markers own, at any depth: every one under `Own[tuple[...]]`, those
    under an `Own[...]` element, none of a plain element. Empty for no
    tuple."""
    bare = bare_tuple(slot)
    if bare is None or slot is None:
        return frozenset()
    return reference_paths(bare) - _declared_borrowed(
        unwrap_ref_type(slot), bare, ())


def owned_leaf_paths(slot: TpyType) -> frozenset[ElemPath]:
    """`owned_slot_paths` over every leaf (`leaf_paths`), value leaves
    included, of a slot that takes ownership: a tuple's `Own`-marked
    leaves, a scalar's one leaf `()`."""
    bare = bare_tuple(slot)
    if bare is None:
        return frozenset({()})
    return leaf_paths(bare) - _declared_borrowed(
        unwrap_ref_type(slot), bare, (), leaf_paths)


def top_level(paths: Iterable[ElemPath]) -> frozenset[int]:
    """The top-level elements the element `paths` lie in."""
    return frozenset(p[0] for p in paths)


class IncomingLends(Protocol):
    """The INCOMING question of a tuple source at an owning sink
    (`tuple_borrowed_paths`): what each scalar member of a literal lends as
    it arrives, and what a call's result does."""

    def member_lends(self, m: TpyExpr) -> bool: ...

    def call_lent_paths(self, call: 'TpyCall | TpyMethodCall',
                        slot: TupleType,
                        prefix: ElemPath = ()) -> frozenset[ElemPath]: ...


def tuple_borrowed_paths(ctx: 'SemanticContext', expr: TpyExpr | None,
                         slot: TpyType | None, prefix: ElemPath = (), *,
                         incoming: IncomingLends | None = None
                         ) -> frozenset[ElemPath] | None:
    """The element paths (`reference_paths`) of a tuple SOURCE at `slot`
    whose reference payload arrives BORROWED -- storage that outlives the
    expression, which an owning slot copies where CPython aliases. None
    when the slot is no tuple. A call lends per its declared return
    (`declared_call_paths`), or as `incoming` answers where the caller
    asks the incoming question -- of an element read off a call
    (`call(...)[0]`) too, projected to its subtree; a select lends what
    either arm does (C++ copies the chosen one); an element the read moves
    out (`element_moves`) and a consuming method's field move lend nothing;
    a name, a field, a subscript and anything else lend every reference
    path.

    A literal answers two questions. Its INCOMING ownership (`incoming`
    given: what each scalar member lends as it arrives, the sink's question)
    asks every member, whatever its capture: a nested tuple member answers
    for its subtree. Its STORAGE after capture (no `incoming`: what a
    binding of it holds, the binding masks' question) lends each member it
    does not capture by VALUE -- a member it captured by value is held as
    an object of its own, a copied one included."""
    bare = bare_tuple(slot)
    if bare is None or expr is None:
        return None
    init = peel_coerce(expr)
    if isinstance(init, TpyIfExpr):
        return frozenset().union(*(
            tuple_borrowed_paths(ctx, arm, bare, prefix,
                                 incoming=incoming) or frozenset()
            for arm in (init.then_expr, init.else_expr)))
    if isinstance(init, TpyTupleLiteral):
        caps = init.elem_capture
        out: set[ElemPath] = set()
        for i, et in enumerate(bare.element_types):
            path = prefix + (i,)
            m = init.elements[i] if i < len(init.elements) else None
            if incoming is None:
                if i < len(caps) and caps[i] is TupleElemCapture.VALUE:
                    continue
            elif m is None:
                continue
            if bare_tuple(et) is not None and m is not None:
                out |= tuple_borrowed_paths(
                    ctx, m, et, path, incoming=incoming) or frozenset()
            elif incoming is None or incoming.member_lends(m):
                out |= _paths_at(et, path)
        return frozenset(out)
    if isinstance(init, (TpyCall, TpyMethodCall)):
        if incoming is not None:
            return incoming.call_lent_paths(init, bare, prefix)
        return declared_call_paths(init, bare, prefix)
    if init in ctx.element_moves or (isinstance(init, TpyFieldAccess)
                                     and init.consuming_move):
        return frozenset()
    if incoming is not None:
        projected = _projected_call_paths(ctx, trace(init), bare, (), False,
                                          incoming)
        if projected is not None:
            return frozenset(prefix + p for p in projected)
    return reference_paths(bare, prefix)


def _projected_call_paths(ctx: 'SemanticContext', t: Trace, slot: TupleType,
                          comp: ElemPath, inside: bool,
                          incoming: IncomingLends
                          ) -> frozenset[ElemPath] | None:
    """The paths of tuple `slot` a value read off a call's result lends as
    it arrives -- an element (`call(...)[0][1]`), a field
    (`call(...).pair`, `call(...)[0].pair`), through the arms of a select
    (`(f(...) if c else g(...))[0]`, either arm's lend) -- projected over
    the one climb (`trace`) with `_Roots.place`'s component rule: a tuple
    subscript is a component `comp` of the call's result, and past a field
    (`inside`) the read lies in the reference leaf at `comp`, lent whole
    when that leaf is. None when the climb takes any other step or ends at
    anything but a call or a ternary."""
    for step in t.steps:
        if step.kind is StepKind.COERCE:
            continue
        if step.kind is StepKind.FIELD:
            if (step.node.hidden_call is not None
                    or is_unbound_field(step)):
                return None
            comp, inside = (), True
            continue
        index = tuple_subscript_index(step.node, ctx.get_expr_type)
        if step.kind is not StepKind.INDEX or index is None:
            return None
        comp = (index,) + comp
    if t.arms:
        if not isinstance(t.end, TpyIfExpr):
            return None
        out: set[ElemPath] = set()
        for arm in t.arms:
            got = _projected_call_paths(ctx, arm, slot, comp, inside,
                                        incoming)
            if got is None:
                return None
            out |= got
        return frozenset(out)
    if not (comp or inside) or not t.ends_at_call or not isinstance(
            t.end, (TpyCall, TpyMethodCall)):
        return None
    whole = bare_tuple(ctx.get_expr_type(t.end))
    if whole is None:
        # A record result: the field read off it is lent with the result.
        return (incoming.call_lent_paths(t.end, slot)
                if inside and not comp else None)
    below = frozenset(p[len(comp):]
                      for p in incoming.call_lent_paths(t.end, whole)
                      if p[:len(comp)] == comp)
    if inside:
        return reference_paths(slot) if below else frozenset()
    return below


def declared_call_paths(call: 'TpyCall | TpyMethodCall', slot: TupleType,
                        prefix: ElemPath = ()) -> frozenset[ElemPath]:
    """The paths of tuple `slot` at `prefix` a call's result lends by its
    declared return (`_declared_borrowed`): `Own[tuple]` nothing, an
    `Own` element marker at any depth not that subtree; every reference
    path of a callee with no declared return."""
    fi = call.resolved_function_info
    if fi is None or fi.return_type is None:
        return reference_paths(slot, prefix)
    return _declared_borrowed(fi.return_type, slot, prefix)


def tuple_source_elem_owned(ctx: 'SemanticContext', expr: TpyExpr | None,
                            slot: TpyType | None) -> tuple[bool, ...] | None:
    """Per element of a tuple source at `slot`, whether the value hands that
    element over as an object of its own: it holds a reference and lends
    none of it (`tuple_borrowed_paths`). None when the slot is no tuple."""
    lent = tuple_borrowed_paths(ctx, expr, slot)
    bare = bare_tuple(slot)
    if lent is None or bare is None:
        return None
    every, lends = top_level(reference_paths(bare)), top_level(lent)
    return tuple(i in every and i not in lends
                 for i in range(len(bare.element_types)))


def tuple_elem_owned(ctx: 'SemanticContext', value: TpyExpr | None,
                     slot: TpyType | None) -> tuple[bool, ...] | None:
    """Per element of a tuple binding, whether the binding holds that
    reference element as an object of its own rather than a borrow of
    storage elsewhere (`tuple_source_elem_owned` of its initializer; a
    name, like every lvalue, owns nothing). None when the slot holds no
    reference element (a value tuple is copied at every binding, so no
    rebind can clobber it)."""
    ref = _reference_elements(slot)
    if ref is None or value is None:
        return None
    owned = tuple_source_elem_owned(ctx, value, slot) or (False,) * len(ref)
    return tuple(r and o for r, o in zip(ref, owned))


def note_tuple_elem_borrows(ctx: 'SemanticContext', name: str,
                            value: TpyExpr | None,
                            slot: TpyType | None) -> None:
    """The ownership fact of a tuple binding, per element path: the
    reference paths its storage holds borrowed after the capture
    (`tuple_borrowed_paths` without `member_lends`) make the local
    non-owning there."""
    borrowed = tuple_borrowed_paths(ctx, value, slot)
    if borrowed:
        note_nonowning_bind(ctx, name, value, borrowed)


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
    (`excluded`), or a name some other statement kind binds. It reads the
    syntax and the bindings' ownership stamps only, so asked during the
    body walk it sees the bindings stamped so far: a yes then is final, a
    no may still turn into one."""
    if func is None or name not in reassigned or name in excluded:
        return False
    bindings = tuple_name_bindings(ctx, func, name)
    return bool(bindings) and any(any(ctx.tuple_elem_owned.get(b) or ())
                                  for b in bindings)


def _kept_places(ctx: 'SemanticContext', value: TpyExpr
                 ) -> list[tuple[TpyExpr, Place | None, int | None]]:
    """The operands a value about to be bound keeps a reference into --
    each arm of a ternary, each element of a tuple literal but a VALUE one
    (a fresh object or a copy, made before anything is rebound), a call
    through what it may retain under its contract (`ContractLend`: not
    what its body is proven to return, so the answer does not depend on
    whether the callee's body was analyzed first) -- each with the one
    place holding it (`Use.HOLDER`; None for a shape that is no place or
    may be several) and the element of the value it lands in (a tuple
    literal's index; None: the whole value)."""
    out: list[tuple[TpyExpr, Place | None, int | None]] = []
    contract = ContractLend(ctx)

    def leaf(e: TpyExpr, part: int | None) -> None:
        roots = storage_roots(peel_coerce(e), Use.HOLDER, None, contract)
        for operand, kept in roots.by_operand():
            out.append((peel_coerce(operand), kept.single_place(), part))

    inner = peel_coerce(value)
    for arm in ((inner.then_expr, inner.else_expr)
                if isinstance(inner, TpyIfExpr) else (inner,)):
        arm = peel_coerce(arm)
        if not isinstance(arm, TpyTupleLiteral):
            leaf(arm, None)
            continue
        caps = arm.elem_capture
        for i, el in enumerate(arm.elements):
            if not (i < len(caps) and caps[i] is TupleElemCapture.VALUE):
                leaf(el, i)
    return out


def kept_elems(ctx: 'SemanticContext', holder: str,
               value: TpyExpr) -> frozenset[int | None]:
    """The elements of `holder`'s current tuple that the value about to
    be bound to it keeps a reference into (`_kept_places`). The tracker
    files no loan whose root is its own holder, so a rebind that keeps
    what it replaces (`p = mixed(9, p[0])`, `p = (Box(9), p[0])`) is seen
    only here. An operand that is no place (`*p`, an operator) keeps the
    whole tuple when it mentions `holder` at all: unknown is kept, never
    "keeps nothing"."""
    return frozenset(
        place.elem if place is not None else None
        for e, place, _ in _kept_places(ctx, value)
        if (place.name == holder if place is not None
            else holder in read_names(e)))


def binding_roots(ctx: 'SemanticContext',
                  value: TpyExpr) -> tuple[str, ...] | None:
    """The names whose storage a binding's value points into
    (`_kept_places`), in source order, an operand that holds no pointer
    (`carries_no_borrow`) left out. None when an operand is no place, or
    when no operand is left: nothing then says where the pointers point."""
    roots: dict[str, None] = {}
    for e, place, _ in _kept_places(ctx, value):
        if carries_no_borrow(ctx, e):
            continue
        if place is None:
            return None
        roots[place.name] = None
    return tuple(roots) or None


def alias_sources(value: TpyExpr | None) -> 'list[str] | None':
    """The names a binding's value is when it is a plain name or a ternary
    of them, else None."""
    e = peel_coerce(value) if value is not None else None
    if isinstance(e, TpyName):
        return [e.name]
    if isinstance(e, TpyIfExpr):
        a = alias_sources(e.then_expr)
        b = alias_sources(e.else_expr)
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
    srcs = alias_sources(value)
    if srcs is not None and (members is None
                             or all(x in members for x in srcs)):
        return _Form.READS
    return _Form.POINTS


def carries_no_borrow(ctx: 'SemanticContext', value: TpyExpr) -> bool:
    """A value that cannot hold a pointer into anything
    (`leaves_hold_no_pointer`)."""
    return not keeps_pointer(ctx.get_expr_type(value), fresh=False)


def _part_type(t: TpyType | None, part: int | None) -> TpyType | None:
    """The type of element `part` of a tuple type (None: the whole)."""
    if t is None or part is None:
        return t
    u = unwrap_readonly(unwrap_ref_type(t))
    if isinstance(u, TupleType) and part < len(u.element_types):
        return u.element_types[part]
    return t


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
        not readonly by its declared type (the binding marks the root
        written, `StatementAnalyzer._mark_rebound_roots`), and bound by
        nothing in the body nor through `nonlocal` in a nested def (an
        `Own` parameter's rebind writes in place under the pointers). Any
        other root -- a local, a
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
        when it may point into every root. An await's result is no
        pointer into anything: the resume binds it whole, a route the
        lowering refuses for a pointer tuple (`ptr_tuple_decision`)."""
        if isinstance(peel_coerce(value), TpyAwait):
            return None
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
    """A body's pointer tuples (`_alias_closure`), the bindings whose roots
    `_RootRule` refuses, and each name the set leaves out for another
    reason with its binding from the set and the member it reads there."""
    names: 'dict[str, PointerTupleOwner]'
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
    # Names out of the set for an element of their own (`owning`) or for
    # bindings whose roots the rule refuses (`root_failed`).
    owning: set[str] = set()
    root_failed: dict[str, list[tuple['TpyVarDecl | TpyAssign', str]]] = {}
    for n, bindings in table.items():
        if not bindings or n in seeds or n in excluded:
            continue
        srcs: list[str] = []
        failed: list[tuple['TpyVarDecl | TpyAssign', str]] = []
        for b in bindings:
            form = _binding_form(ctx, b, None)
            if form is _Form.OWNS:
                owning.add(n)
                break
            value = binding_value(b)
            assert value is not None
            if form is _Form.READS:
                srcs.extend(alias_sources(value) or ())
                continue
            why = rule.first_refused(ctx, n, value)
            if why is not None:
                failed.append((b, why))
        else:
            if failed:
                root_failed[n] = failed
            else:
                reads[n] = srcs
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
    mixed: list[tuple['TpyVarDecl | TpyAssign', str, str]] = []
    for n, bindings in table.items():
        if not bindings or n in out:
            continue
        sources = [(b, alias_sources(binding_value(b)) or [])
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
    return _Closure(out, tuple(refused), tuple(mixed))


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
    """THE pointer tuples of a function body, each mapped to the
    owner whose element storage it holds: every name `rebinds_owning_tuple`
    decides (itself), and every name that owns no element at any of its
    bindings and is bound from one of them at one (`q = p; q = h.pair`,
    `_alias_closure`): it holds their element pointers or other storage's,
    never an object of its own -- in a nested def, the enclosing
    body's pointer tuples it captures included. Sema's copy warning and
    capture check and the binding planner read this one answer. Once the outermost body is analyzed (`finalize_pointer_tuples`)
    it is cached; asked during the walk it is the answer over the bindings
    stamped so far, which only grows."""
    if func is None:
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
    whatever the order of its bindings and closures: its seeds (the owning
    pointer tuples, kept for the outermost body's pass); for the outermost
    body, the final sets of it, its nested defs and generator expressions,
    the root rule's refusals, the declared-slot checks of the rebinds the
    final set leaves out (`TypeCompatibility.park_repointed_tuple_check`),
    and the closure rejects. The roots a binding points into are marked
    written by the body walk, at the binding
    (`StatementAnalyzer._mark_rebound_roots`)."""
    # The ownership fact is complete once the body is: the owning-sink
    # reads it settles may declare copies the checks below read.
    compat.settle_owning_reads(func.body if func is not None else [])
    if func is None:
        return
    f = ctx.func
    excluded = frozenset(f.current_param_names | f.current_nonlocal_names)
    owners = _owners(ctx, func, f.current_reassigned_vars, excluded)
    ctx.pointer_tuple_seeds[func] = (owners, excluded)
    if nested:
        return
    closure = _alias_closure(ctx, func, owners, excluded)
    final = closure.names
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
        tuple_name_bindings(ctx, nd.func, "")
        inner.append((nd.func, func, nd.captured_names))
    inner += [(g.func, g.creator, g.reads_creator)
              for g in ctx.genexpr_creators.pop(func, ())]
    by_func = IdentityMap((fn, c.names) for fn, c in sets)
    for fn, creator, reads in inner:
        src = by_func.get(creator) or {}
        inherited = {n: src[n] for n in reads if n in src}
        if fn.is_genexpr:
            inner_closure = _Closure(inherited, ())
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


def _loop_shown(loop: TpyForEach) -> str:
    """A loop's iterator, in the program's words."""
    return (f"the loop over `{loop.iter_text}`" if loop.iter_text is not None
            else "this loop's iterator")


def _keeps(name: str, owner: PointerTupleOwner, *,
           itself: bool = True) -> str:
    """What a pointer tuple holds, as the predicate of a clause about it."""
    home = f"'{owner.home}' itself" if itself else f"'{owner.home}'"
    if owner.name == name:
        return f"keeps objects of its own in storage of {home}"
    return f"may hold the objects '{owner.name}' keeps in storage of {home}"


def _holds(name: str, owner: PointerTupleOwner, *,
           itself: bool = True) -> str:
    return f"which {_keeps(name, owner, itself=itself)}"


def _refuse_nonlocal_rebinds(ctx: 'SemanticContext', func: TpyFunction,
                             names: 'Mapping[str, PointerTupleOwner]',
                             defs: 'list[TpyNestedDef]') -> None:
    """A nested def rebinding a pointer tuple or one of its aliases through
    `nonlocal` is a binding of the enclosing name that the enclosing body's
    pointer-tuple facts never see -- the roots its bindings point into, so
    `q = h.pair` in the closure would aim a mutable pointer tuple into a
    parameter left `const` -- so it is a located error at the closure's
    binding."""
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
    if isinstance(peel_coerce(value), TpyNoneLiteral):
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
    reference local as a site: it takes storage of its OWN, which only MIR
    may turn into a write in place."""
    owned = tuple_elem_owned(ctx, value, var_type)
    if owned is not None:
        ctx.tuple_elem_owned[stmt] = owned
    note_tuple_elem_borrows(ctx, name, value, var_type)
    kind = bind_kind_of(ctx, value, owned)
    ctx.func.bind_kinds[stmt] = kind
    if _note_rebind_site(ctx, stmt, name, kind, var_type, owned,
                         rebind=rebind):
        stmt.rebind_storage = RebindStorage.OWN


def stamp_unpack_rebinds(ctx: 'SemanticContext', stmt: TpyTupleUnpack) -> None:
    """Mark each unpack target that rebinds a reference local with an
    element it takes over (`is_owned`, moved out of the source) as a site,
    as `stamp_bind_kind` does an assignment's. A borrowed element aliases
    the source's object rather than putting one of its own in the slot."""
    for i, name in enumerate(stmt.targets):
        if name is None or stmt.is_new[i]:
            continue
        kind = BindKind.RVALUE if stmt.is_owned[i] else BindKind.LVALUE
        _note_rebind_site(ctx, stmt, name, kind, stmt.target_types[i], None,
                          rebind=True)


def stamp_capture_rebind(ctx: 'SemanticContext', stmt: TpyMatch, name: str,
                         var_type: TpyType | None) -> None:
    """Mark a `match` capture that rebinds the reference local `name` with
    a part of a fresh subject as a site, as `stamp_bind_kind` does an
    assignment's. A capture of an lvalue subject aliases it."""
    _note_rebind_site(ctx, stmt, name, bind_kind_of(ctx, stmt.subject),
                      var_type, None, rebind=True)


def _note_rebind_site(ctx: 'SemanticContext', stmt: TpyStmt, name: str,
                      kind: BindKind | None, var_type: TpyType | None,
                      owned: tuple[bool, ...] | None, *, rebind: bool) -> bool:
    """Record `name`'s binding at `stmt` as a site when it rebinds a
    reference local with an rvalue: the new object takes storage of its
    OWN, which only MIR may turn into a write in place. A module-level
    statement's binding is a global's, whose every write takes a static
    slot of its own and runs once: no site."""
    if not (rebind and kind is BindKind.RVALUE
            and is_reference_local(var_type, owned)
            and not ctx.is_module_slot_stmt(stmt)):
        return False
    ctx.func.gate_sites[stmt] = (ctx.func.gate_sites.get(stmt, frozenset())
                                 | {name})
    return True


def _live_after_site(stmt: TpyStmt, name: str) -> frozenset[Holder] | None:
    """The names live right after `stmt` binds `name`: a match capture's
    are those of every arm that captures it (it binds at the arm's entry);
    None where liveness never walked the body."""
    if not isinstance(stmt, TpyMatch):
        return stmt.live_names_after  # type: ignore[attr-defined]
    live: set[Holder] = set()
    for case in stmt.cases:
        if not any(cap.name == name
                   for cap in iter_capture_bindings(case.pattern)):
            continue
        if case.live_names_after is None:
            return None
        live |= case.live_names_after
    return frozenset(live)


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
    loans: dict[_ReplayHolder, frozenset[_Loan]] = field(default_factory=dict)

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


@dataclass(eq=False)
class _Site:
    """One name a site binds. `value`: what the statement binds it from
    (an unpack's whole source, a capture's subject); `holder_type`: the
    name's type at a binding whose value is not the name's alone (an
    unpack target's element, a capture), None for a declaration or an
    assignment. `state` is joined over every pass reaching the site, and
    `kept` holds the elements of the name's current tuple the new value
    keeps (`kept_elems`)."""
    stmt: TpyStmt
    name: str
    value: TpyExpr | None
    holder_type: TpyType | None
    state: _State | None = None
    kept: frozenset[int | None] = frozenset()


class _Replay:
    def __init__(self, ctx: 'SemanticContext', always_foreign: set[str]):
        self.ctx = ctx
        self.bind_kinds = ctx.func.bind_kinds
        self.stmt_loans = ctx.func.borrow_tracker.stmt_loans
        self.gate_sites = ctx.func.gate_sites
        self.always_foreign = always_foreign
        self.ids: IdentityMap = IdentityMap()
        self.by_id: dict[int, object] = {}
        # Every name a statement binds at a site, in walk order; `site_of`
        # finds a statement's sites by name.
        self.sites: list[_Site] = []
        self.site_of: 'IdentityMap[TpyStmt, dict[str, _Site]]' = IdentityMap()
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
        # Each loop's running iterator, numbered in walk order.
        self.iter_ordinals: IdentityMap = IdentityMap()

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
            src = peel_coerce(s.value)
            for i, name in enumerate(s.targets):
                if name is not None:
                    takes = i < len(s.is_owned) and s.is_owned[i]
                    # A target that does not take the element over aliases
                    # the source's element, whether or not the tracker
                    # filed the loan (a module-level target is a global).
                    alias = (frozenset((_Loan(
                        StorageKey.of_name(src.name), BorrowKind.ALIAS,
                        self.origins_of(StorageKey.of_name(src.name), st),
                        i),))
                        if isinstance(src, TpyName) and src.name != name
                        and not takes else frozenset())
                    holder_type = (s.target_types[i]
                                   if i < len(s.target_types) else None)
                    kind = BindKind.RVALUE if takes else BindKind.LVALUE
                    if self.is_site(s, name, kind,
                                    self.local_type(name, None, holder_type),
                                    None):
                        self.note_site(s, name, s.value, holder_type, st)
                    # Each target holds ONE element (its loan carries the
                    # index), not the whole tuple.
                    self.bind_foreign(
                        s, name, s.value, st, part=i, extra=alias,
                        holds=not takes, holder_type=holder_type, owns=takes)
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
                    self.bind_foreign(s, item.target, item.context_expr, st,
                                      holder_type=item.enter_type)
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
        # Call-blind: a loop over a call is "untraced" here. Following the
        # call's lends makes the loop-source write proof
        # (`resolve_source_writes`) copy a value loop variable it moved
        # (`for x in v` under `for v in m.values()`, `out.append(x)`).
        self.for_edges.append((bound_names_of(s), advanced_root(s)))
        self.expr_effects(s.iterable, st, s, top_is_bind=True)
        groups = self.groups(s)
        # The running iterator is a holder bound from the iterable, alive
        # until the loop exits. It has no type of its own to exempt it: an
        # iterator over a `str` points into the string. A fresh iterable
        # (a call result) is the object the iterator runs over, so its own
        # type says whether that object can keep a pointer.
        iter_key = self.iter_holder(s)
        holds = (bind_kind_of(self.ctx, s.iterable) is not BindKind.RVALUE
                 or keeps_pointer(self.ctx.get_expr_type(s.iterable),
                                  fresh=True))
        self.bind_iterator(s, iter_key, st, holds=holds)

        # The iterable is evaluated once, before the first pass: what the
        # variable borrows is the storage its source held then, never what a
        # rebind in the body has put there since.
        var_loans = self.loans_of(s.var, groups, s.iterable, st)
        var_loans |= self.path_loans(s.var, s.iterable, st, s.elem_type,
                                     None, fresh=False, held=var_loans)

        def at_head(state: _State) -> None:
            self.bind_foreign(s, s.var, s.iterable, state,
                              holder_type=s.elem_type)
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
            self.expr_effects(s.condition, state, s)

        head, loop = self.walk_loop_body(s, st, at_head)
        exit_st = head.copy()
        at_head(exit_st)
        out = self.walk_stmts(s.orelse, exit_st)
        return _join_all([out, *loop.breaks])

    def walk_match(self, s: TpyMatch, st: _State) -> _State | None:
        self.expr_effects(s.subject, st, s)
        groups = self.groups(s)
        # A capture of a fresh subject holds an object of its own.
        kind = bind_kind_of(self.ctx, s.subject)
        outs: list[_State | None] = [] if s.is_exhaustive else [st.copy()]
        for case in s.cases:
            cs = st.copy()
            # Which arm a capture belongs to is not recorded; binding every
            # arm's captures into each arm over-approximates harmlessly.
            for holder in groups:
                self.bind_foreign(s, holder, None, cs)
            # A capture is bound to the subject or to storage inside it.
            for cap in iter_capture_bindings(case.pattern):
                if not cap.bind_by_value:
                    if self.is_site(s, cap.name, kind,
                                    self.local_type(cap.name, None,
                                                    cap.bound_type), None):
                        self.note_site(s, cap.name, s.subject, cap.bound_type,
                                       cs)
                    self.bind_foreign(s, cap.name, s.subject, cs,
                                      holder_type=cap.bound_type,
                                      loans_from_source=False,
                                      owns=kind is BindKind.RVALUE)
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

    def groups(self, stmt: TpyStmt
               ) -> dict[Holder, list[tuple[StorageKey, LoanInfo]]]:
        """The loans the tracker filed at `stmt`, by holder. A running
        iterator filed there is a holder of the replay only when `stmt` is
        its own `for`: a comprehension's iterator ends inside the statement
        that runs it."""
        out: dict[Holder, list[tuple[StorageKey, LoanInfo]]] = {}
        for storage, holder, info in self.stmt_loans.get(stmt, ()):
            if isinstance(holder, IterHolder) and holder.node is not stmt:
                continue
            out.setdefault(holder, []).append((storage, info))
        return out

    def iter_holder(self, loop: TpyForEach) -> IterHolder:
        if loop not in self.iter_ordinals:
            self.iter_ordinals[loop] = len(self.iter_ordinals)
        return IterHolder(loop)

    def bind_iterator(self, s: TpyForEach, holder: IterHolder, st: _State,
                      *, holds: bool) -> None:
        """`s`'s running iterator holds what the tracker filed for it and,
        with `holds`, what it may keep of the iterable. It binds no name:
        it owns no storage and nothing deletes or rebinds it."""
        loans = self.loans_of(holder, self.groups(s), s.iterable, st)
        if holds:
            loans |= self.path_loans(holder, s.iterable, st, None, None,
                                     fresh=False, held=loans)
        st.loans[holder] = loans

    def bind(self, stmt: TpyStmt, name: str, value: TpyExpr | None,
             kind: BindKind | None, st: _State, node: object) -> None:
        if value is not None:
            self.expr_effects(value, st, stmt, top_is_bind=True)
        if node is stmt and self.is_site(
                stmt, name, kind, self.local_type(name, value),
                self.ctx.tuple_elem_owned.get(node)):
            self.note_site(stmt, name, value, None, st)
        loans = self.loans_of(name, self.groups(stmt), value, st)
        # A fresh object holds copies (`xs = [p]`) unless its type can point
        # (`w = W(t[0].s)` with a `StrView` field).
        if value is not None and kind is not BindKind.NONE:
            loans |= self.path_loans(name, value, st,
                                     self.local_type(name, value), None,
                                     fresh=kind is BindKind.RVALUE,
                                     held=loans)
        st.loans[name] = loans
        self.bind_storage(name, node if kind is BindKind.RVALUE else None, st)

    def bind_storage(self, name: str, owner: object | None,
                     st: _State) -> None:
        """`name` now holds storage `owner` (a binding node) put an object
        of its own in, or with no owner storage it does not own."""
        if owner is not None and name not in self.always_foreign:
            st.origins[name] = frozenset((self.id_of(owner),))
        else:
            st.origins[name] = frozenset((FOREIGN,))
            self.foreign_any.add(name)
        self.bound_here.add(name)
        self.bound_any.add(name)

    def is_site(self, stmt: TpyStmt, name: str, kind: BindKind | None,
                var_type: TpyType | None,
                owned: tuple[bool, ...] | None) -> bool:
        """`name`'s binding at `stmt` is a site the residual clobber is
        asked of: the site rule recorded it (`_note_rebind_site`), or it is
        a first bind inside a loop body, which runs again on the next
        iteration and reuses its storage like any rebind -- a holder still
        reading the previous iteration's object is the same question, asked
        of the same site."""
        if name in self.gate_sites.get(stmt, ()):
            return True
        return (bool(self.loops) and kind is BindKind.RVALUE
                and is_reference_local(var_type, owned))

    def note_site(self, stmt: TpyStmt, name: str, value: TpyExpr | None,
                  holder_type: TpyType | None, st: _State) -> None:
        """Record the state reaching `name`'s binding at site `stmt`, before
        the binding writes it."""
        sites = self.site_of.setdefault(stmt, {})
        site = sites.get(name)
        if site is None:
            site = sites[name] = _Site(stmt, name, value, holder_type)
            self.sites.append(site)
        site.state = _join(site.state, st)
        if value is not None:
            site.kept |= kept_elems(self.ctx, name, value)

    def bind_foreign(self, stmt: TpyStmt, name: str, source: TpyExpr | None,
                     st: _State, *, part: int | None = None,
                     extra: frozenset[_Loan] = frozenset(),
                     holds: bool = True,
                     holder_type: TpyType | None = None,
                     loans_from_source: bool = True,
                     owns: bool = False) -> None:
        """`part`: the element of `source` an unpack target binds (None:
        all of it). `holds`: the name may refer into `source` (not an unpack
        target taking its element over).
        `loans_from_source`: the name's loans are read off `source` too,
        not only the ones the tracker filed for it (a match capture's
        are). `owns`: the binding puts an object of its own in the name's
        storage (an unpack target taking its element over, a capture of a
        fresh subject), so its storage is the statement's, not foreign."""
        loans = self.loans_of(
            name, self.groups(stmt), source if loans_from_source else None,
            st, whole=part is None) | extra
        if source is not None and holds:
            loans |= self.path_loans(name, source, st, holder_type, part,
                                     fresh=False, held=loans)
        st.loans[name] = loans
        self.bind_storage(name, stmt if owns else None, st)

    def path_loans(self, holder: Holder, value: TpyExpr, st: _State,
                   holder_type: TpyType | None, part: int | None, *,
                   fresh: bool, held: frozenset[_Loan]) -> frozenset[_Loan]:
        """The loans on owned tuple elements that `holder`, bound from
        `value`, keeps by a path the tracker files none for (`saved =
        t[0].xs[1]`, a match capture, an unpack target, a loop's iterator):
        one per element of each place `_kept_places` resolves that the
        storage the tuple holds in `st` owns and no loan in `held` already
        covers (`loan_elems`). A path the place walk does not resolve (no
        place, or no literal index) reaches every element the tuple owns --
        unknown is held, never "holds nothing". What the holder keeps of an
        operand is judged by the holder's own type there (`keeps_pointer`):
        a `StrView` bound from a `str` field points into it; a pending or
        unknown type may point. An unpack target (`part`) receives one
        element of the value whole, and nothing of an operand landing in
        another target. The tuple's own rebind keeping part of what it
        replaces is `kept_elems`'s, not a loan."""
        if not keeps_pointer(holder_type, fresh=fresh):
            return frozenset()
        types: dict[str, TpyType | None] = {}

        def visit(e: TpyExpr) -> bool:
            if isinstance(e, TpyName) and e.name not in types:
                types[e.name] = self.local_type(e.name, e)
            return True

        walk_expr_tree(value, visit)
        out: set[_Loan] = set()
        for e, place, lands in _kept_places(self.ctx, value):
            if part is None:
                kept_type = _part_type(holder_type, lands)
            elif lands is None or lands == part:
                kept_type = holder_type
            else:
                continue
            if not keeps_pointer(kept_type, fresh=fresh):
                continue
            if place is None:
                reached: list[tuple[str, int | None]] = [
                    (r, None) for r in sorted(read_names(e))]
            else:
                root, elem = place.name, place.elem
                # An unpack target holds one element of a tuple it names.
                if (elem is None and part is not None and lands is None
                        and isinstance(peel_coerce(e), TpyName)):
                    elem = part
                reached = [(root, elem)]
            for root, elem in reached:
                if root == holder:
                    continue
                key = StorageKey.of_name(root)
                origins = self.origins_of(key, st)
                var_type = types.get(root)
                need = self.owned_at(var_type, origins)
                if elem is not None:
                    need &= {elem}
                for loan in held | out:
                    need -= self.loan_elems(loan, root, origins, var_type)
                out.update(_Loan(key, BorrowKind.ALIAS, origins, i)
                           for i in need)
        return frozenset(out)

    def bind_other_holders(self, stmt: TpyStmt, st: _State) -> None:
        """Loans registered on a statement for a holder the statement's own
        shape did not bind (a capture, a target bound by a helper)."""
        for holder in self.groups(stmt):
            if holder not in self.bound_here:
                self.bind_foreign(stmt, holder, None, st)

    def loans_of(self, holder: Holder,
                 groups: dict[Holder, list[tuple[StorageKey, LoanInfo]]],
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
        inner = peel_coerce(value)
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
            arm = peel_coerce(arm)
            if isinstance(arm, TpyName) and arm.name != holder:
                arm_key = StorageKey.of_name(arm.name)
                origins = self.origins_of(arm_key, st)
                for i in self.owned_at(self.local_type(arm.name, arm),
                                       origins):
                    out.add(_Loan(arm_key, BorrowKind.ALIAS, origins, i))
        # A generator object keeps what its frame borrows for as long as it
        # lives, and any call result keeps what it may point into
        # (`c = mk(g)` where `mk` returns a coroutine over it).
        for root in (frame_operand_keys(inner)
                     + _call_loan_keys(self.ctx, inner)):
            if root.as_name() != holder:
                out.add(_Loan(root, BorrowKind.OPAQUE,
                              self.origins_of(root, st)))
        if holder in self.frames:
            # What sema traced the frame to (a loop variable over a
            # container read as the container) -- the storage a rebind of
            # that name would pull out from under it.
            fact = self.ctx.func.frame_local_roots.get(holder)
            for root in fact[0] if fact is not None else ():
                if root is not None and root != holder:
                    key = StorageKey.of_name(root)
                    out.add(_Loan(key, BorrowKind.OPAQUE,
                                  self.origins_of(key, st)))
        return frozenset(out)

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

    def origins_of(self, key: StorageKey, st: _State) -> frozenset[int]:
        return st.origins.get(key.root, frozenset((FOREIGN,)))

    # -- expressions --

    def expr_effects(self, e: TpyExpr, st: _State, stmt: TpyStmt,
                     top_is_bind: bool = False,
                     bound: 'IdentitySet[TpyExpr] | None' = None) -> None:
        """Replay a walrus binding and a pointer escaping through a call
        argument, in evaluation order. What a bind's value climbs through to
        its storage (`_bound_nodes`, computed at the top node of the bind)
        is the holder's own loan, registered by the tracker, not an
        escape."""
        if top_is_bind:
            bound = _bound_nodes(self.ctx, e)
        inner = peel_coerce(e)
        if isinstance(inner, TpyNamedExpr):
            self.bind(stmt, inner.target, inner.value,
                      self.bind_kinds.get(inner), st, inner)
            return
        if bound is None or e not in bound:
            for name in _ptr_escape_roots(self.ctx, e):
                root = StorageKey.of_name(name)
                st.loans[_ESCAPED] = st.loans.get(_ESCAPED, frozenset()) | {
                    _Loan(root, BorrowKind.PTR, self.origins_of(root, st))}
        # A generator or coroutine handed over to an owning parameter
        # (`create_task(consume(g))`) outlives the statement, and so do its
        # loans; one lent to a borrowing parameter (`list(relay(g))`) dies
        # with the statement.
        for arg in _owned_args(inner):
            arg = peel_coerce(arg)
            roots = (frame_operand_keys(arg)
                     + _call_loan_keys(self.ctx, arg))
            if isinstance(arg, TpyName) and arg.name in self.frames:
                # The generator or coroutine object itself changes hands.
                roots.append(StorageKey.of_name(arg.name))
            for root in roots:
                st.loans[_ESCAPED] = st.loans.get(_ESCAPED, frozenset()) | {
                    _Loan(root, BorrowKind.OPAQUE, self.origins_of(root, st))}
        # TODO: walk through parse.nodes.walk_expr_tree (the shared pruning visitor) instead of an own children() loop.
        for child in inner.children():
            self.expr_effects(child, st, stmt, bound=bound)
        if isinstance(inner, TpyAwait):
            # `await c` runs the coroutine to its end: it holds nothing after.
            awaited = peel_coerce(inner.value)
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
                 {r.root for r, *_ in self.hold_loans(holder)})
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
        """Warn on the residual clobber at every site: every site takes a
        slot of its own, so a holder still reading the object an earlier
        pass of the same site bound is clobbered when the site runs again.
        Record, per tuple site of a body, the holder its lowering names
        when it refuses the site (`tuple_own_sites`), and refuse rebinding
        a generator name."""
        frame_sites = self.ctx.func.frame_rebind_sites
        # `outer = p` with `p` bound in the loop body is diagnosed at the
        # alias site by the scope-escape check, which hoists `p` for it;
        # the clobber this site would report for THAT holder is the same
        # alias. Keyed on the pair: another holder of `p` is not covered.
        escape_warned = self.ctx.func.escape_warned_aliases
        for site in self.sites:
            stmt, name, st = site.stmt, site.name, site.state
            var_type = self.local_type(name, site.value, site.holder_type)
            if st is None:
                continue  # unreachable
            is_tuple = _reference_elements(var_type) is not None
            tuple_site = (is_tuple and not self.ctx.is_top_level
                          and getattr(stmt, "rebind_storage", None)
                          is RebindStorage.OWN)
            origins = st.origins.get(name)
            if not origins:
                # The name holds nothing here: never bound on any path (the
                # `else` arm's first binding -- nothing an alias could see)
                # or deleted (`del p`, an empty set), where an alias of the
                # deleted tuple's objects may still read them.
                if origins is not None and tuple_site:
                    self.ctx.tuple_own_sites[stmt] = None
                continue
            # A generator name that holds nothing on every path reaching
            # here (`else: h = gen(xs)` after `if c: h = gen(xs)`) is
            # bound for the first time, not rebound.
            if stmt in frame_sites and origins - {UNBOUND}:
                # A slot of its own would outlive the name's, and rebuilding
                # the frame in place would need proof that nothing -- an
                # alias, a frame, a task, a view of a yielded value -- still
                # reaches the old generator; a generator name is bound once.
                raise self.ctx.error(
                    f"cannot rebind '{name}' to a new generator: a name "
                    f"holding a generator is bound once, since something "
                    f"may still reach the old one; bind the new generator "
                    f"to a new name",
                    stmt)
            # None: liveness never walked the body -- stay quiet.
            live = _live_after_site(stmt, name)
            site_id = self.ids.get(stmt)
            # The objects this site's own earlier passes bound, per element
            # a tuple's binding owns (None for a scalar).
            own_elems = (self.owned_at(var_type, frozenset((site_id,)))
                         if is_tuple and site_id is not None else set())
            # The holders whose loan points into what the name holds here.
            own_by: set[_ReplayHolder] = set()
            clobbered: dict[Holder, BorrowKind] = {}
            for holder, loans in st.loans.items():
                # A loop's iterator is live where its loop can pull from it
                # again (`liveness.loop_iterator`).
                read_again = live is not None and holder in live
                for loan in loans:
                    root, kind, lorigins, elem = loan
                    if not _loan_on(root, name):
                        continue
                    if is_tuple and self.loan_elems(loan, name, origins,
                                                    var_type):
                        own_by.add(holder)
                    # A loan on this very site's slot that is still read
                    # after the site runs again: one slot cannot hold both.
                    # A NAMED call result holding the slot (OPAQUE) is
                    # clobbered the same way; unknown borrow facts count as
                    # aliased, so the warning may be conservative there. An
                    # escaped holder (a pointer handed out through a call, a
                    # frame consumed by a call) has no liveness to consult:
                    # it is too coarse for a warning.
                    if not (read_again and holder is not _ESCAPED
                            and site_id in lorigins
                            and not (kind is BorrowKind.ALIAS
                                     and (holder, name) in escape_warned)):
                        continue
                    if not is_tuple or (own_elems if elem is None
                                        else own_elems & {elem}):
                        clobbered[holder] = kind
            if tuple_site:
                # Only the pointer tuple's elements have storage of their
                # own; any other tuple (an `Own` parameter, a loop variable,
                # a name the pointer-tuple form refuses) is refused at the
                # site by its lowering, naming the holder.
                named = sorted(h for h in own_by if isinstance(h, str))
                # A pointer handed out of the tuple or an iterator over it
                # holds the object with no name to report.
                self.ctx.tuple_own_sites[stmt] = (
                    named[0] if named
                    else UnnamedHolder.POINTER_OR_ITERATOR if own_by
                    else None)
            if clobbered:
                self.warn(site, var_type, clobbered,
                          whole_object=not is_tuple)

    def loan_elems(self, loan: _Loan, name: str, origins: frozenset[int],
                   var_type: TpyType | None) -> set[int]:
        """The elements of tuple `name`, its storage one of `origins`, that
        the storage verdict sees `loan` point into: the ones the tuple owns
        in storage the loan was taken on (a loan through a borrowed element
        points elsewhere; one on storage the name no longer holds is on
        another object), narrowed to the loan's element."""
        if not _loan_on(loan.root, name):
            return set()
        owned = self.owned_at(var_type, loan.origins & origins)
        return owned if loan.elem is None else owned & {loan.elem}

    def local_type(self, name: str, value: TpyExpr | None,
                   holder_type: TpyType | None = None) -> TpyType | None:
        """The local's DECLARED slot type -- what the slot may hold at the
        site, wider than what a rebind's own rvalue stores. Undeclared, it
        is the type the binding gives it: `holder_type` where the binding
        holds part of `value` (an unpack target, a capture), else
        `value`'s."""
        t = self.ctx.local_decl_type(name)
        if t is None:
            t = holder_type
        if t is None and value is not None:
            t = self.ctx.get_expr_type(value)
        return t

    def warn(self, site: _Site, var_type: TpyType | None,
             clobbered: dict[Holder, BorrowKind], *,
             whole_object: bool) -> None:
        stmt, name = site.stmt, site.name
        # A variable is named before a loop's iterator, an outer loop's
        # iterator before an inner one's; loops at one location (or none)
        # in walk order. Every iterator the replay holds was bound by
        # `walk_for`, so its loop has an ordinal.
        def order(h: Holder) -> tuple[bool, int, int, int, str]:
            if isinstance(h, IterHolder):
                loc = h.node.loc
                return (True, loc.line if loc else 0,
                        loc.column if loc else 0, self.iter_ordinals[h.node],
                        "")
            return (False, 0, 0, 0, h)

        alias = min(clobbered, key=order)
        if alias == name and site.kept:
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
        if isinstance(alias, IterHolder):
            self.ctx.warning(
                f"{_loop_shown(alias.node)} will not keep iterating the object it "
                f"was given -- '{name}' is rebound here into that object's "
                f"storage; bind the new value to a name of its own", stmt)
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
    """The arguments of call `e` its callee takes by ownership."""
    ops = call_operands(e)
    return ([] if ops is None else
            [a for i, a in ops.positioned() if ops.takes_ownership(i)])


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


def _call_loan_keys(ctx: 'SemanticContext', e: TpyExpr) -> list[StorageKey]:
    """The storage a call result `e` may keep (`call_loans` under MAY); a
    guess about a pending callee counts: every loan here is OPAQUE."""
    return [loan.key for loan in call_loans(e, MayLend(ctx))
            if isinstance(loan.key, StorageKey)]


def _ptr_escape_roots(ctx: 'SemanticContext', e: TpyExpr) -> list[str]:
    """Storage roots a pointer or view into them is minted from by this
    node: an address-taking coercion (`Coercion.produces_non_null_ptr`), a
    borrowing constructor (`borrowing_ctor_arg`: `Ptr(x)`, `take_ptr(x)`,
    `StrView(s)`), a slice view. Where such a node is not part of a
    binding's own value, the pointer went somewhere the tracker cannot see
    (a call argument, a container), so its root is held for the rest of
    the body."""
    if isinstance(e, TpyCoerce) and e.coercion.produces_non_null_ptr:
        return _held_roots(ctx, e.expr)
    inner = peel_coerce(e)
    arg = borrowing_ctor_arg(inner)
    if arg is not None:
        return _held_roots(ctx, arg)
    if isinstance(inner, TpySubscript) and isinstance(inner.index, TpySlice):
        return _held_roots(ctx, inner.obj)
    return []


def _bound_nodes(ctx: 'SemanticContext',
                 value: TpyExpr) -> IdentitySet[TpyExpr]:
    """The nodes a binding's `value` climbs through to the storage it
    holds (`leaves` under MAY: its steps, the calls it forwards through and
    the operands they lend), by identity: a pointer minted there is the
    binding's own loan, not an escape."""
    out: IdentitySet[TpyExpr] = IdentitySet([value])
    for leaf in leaves(value, MayLend(ctx), split=False):
        out.update(step.node for step in leaf.path)
        out.add(leaf.node)
    return out


def _held_roots(ctx: 'SemanticContext', e: TpyExpr) -> list[str]:
    """The storage a pointer minted from `e` keeps alive (`Use.HOLDER`); an
    operand no place names keeps every name it reads alive."""
    roots = storage_roots(e, Use.HOLDER, None, MayLend(ctx))
    return roots.names + [n for u in roots.unknown for n in read_names(u)
                          if n not in roots.names]


def decide_rebind_storage(ctx: 'SemanticContext', stmts: list[TpyStmt], *,
                          always_foreign: set[str] = frozenset(),
                          initial_foreign: set[str] = frozenset()) -> None:
    """Replay a body whose stamped sites all keep OWN storage and warn on
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
