"""Fact 1 of the ownership model: which element paths of a binding's
STORAGE the binding owns (`BindingOwnership`), one verdict per binding.

A binding owns path `p` when its value there was ACQUIRED as the binding's
own and no later binding made `p` refer to storage that already exists.
There are three acquisitions, each read where its fact lives:

* handed over by the caller -- an ownership-transfer parameter
  (`param_ownership`), the consuming receiver, a lambda's ownership-transfer
  parameter (`lambda_param_ownership`): the signature, decided before the
  body;
* copied or constructed into the binding -- a local sema made an object of
  its own (`ever_owned_locals`; a generator object owns its frame,
  `owned_generator_locals`), a by-value Optional parameter's own copy, a
  copied iteration step (`copies_var`, `target_copies`), a value `with`
  target or by-value `match` capture: body facts;
* transferred from an iteration -- a consuming loop, an `Own`
  element, an owned loop-head or clause-head target (`is_owned`), a clause
  its iteration hands elements to (`step_handover`): the loop's settled
  mode.

A LOCAL is one Python variable across its function, so its entry reads the
function-wide rebinding fact (`NonOwningBind`). A binding site's entry is
its own, keyed by its node: an iteration binder reads only the rebinds
inside its own body (a later `x = borrowed` after a consuming `for x in
xs` does not revoke the loop's ownership), and a `with` item, an
`except` handler and a `match` case key their targets, which a read in
their block resolves to. Not owned: an alias, an `except` target, a
global, a parameter that takes nothing over, a declaration that binds a
call's `val_or_ref_t` result itself (`binds_val_or_ref`).

The fact says nothing about whether a READ may consume what the binding
owns: that is the read's (the source record's MOVE rule) and the binding
planner's representation (`thir/lower/bindings.py`, which reads this as
the base of `movable`).

One writer, `record_body_ownership`, runs at the end of
`TypeCompatibility.settle_owning_reads`, once a body's facts are complete;
the tables live on the module's `SemanticContext` (`body_ownership`,
`site_ownership`).
"""

from __future__ import annotations

from collections.abc import Iterable, Set as AbstractSet
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Mapping

from ..identity_map import IdentityMap, IdentitySet
from ..parse.nodes import (ResultForm, TpyCall, TpyDictComprehension,
                           TpyExpr, TpyForEach,
                           TpyFunction, TpyGeneratorExpression, TpyIfExpr,
                           TpyListComprehension, TpyMatch, TpyMethodCall,
                           TpyName, TpyNamedExpr, TpyNestedDef,
                           TpyComprehensionGenerator,
                           TpySetComprehension, TpyStmt, TpyTry,
                           TpyTupleUnpack, TpyVarDecl, TpyWith, TpyAssign,
                           iter_capture_bindings, walk_body_stmts,
                           walk_expr_tree, written_names)
from .. import modules as builtin_modules
from ..typesys import (OptionalType, OwnType,
                       global_binds_by_reference,
                       ReadonlyType, TpyType, TypeParamRef,
                       param_takes_ownership,
                       unwrap_own,
                       unwrap_readonly,
                       unwrap_ref_type, unwrap_send_sync,
                       view_family_for_type)
from ..value_category import (own_iter_source, peel_coerce,
                              peel_value_wrappers, peeled_read)
from .elem_paths import ElemPath, bare_tuple, leaf_paths, owned_leaf_paths
from .iter_loans import IterElementSource, iter_element_source
from .context import NonOwningBind, fold_nonowning
from .call_lends import callee_lend_fact
from .decl_types import declared_bound_type
from .scope_tracker import binds_module_storage
from .read_binding import ReadBinding, ReadKind, resolve_read

if TYPE_CHECKING:
    from .context import FunctionTrackingState, SemanticContext


class Acquired(Enum):
    """How a binding acquired what it owns."""
    HANDED = "handed"                # the caller handed it over
    COPIED_PARAM = "copied_param"    # a by-value Optional parameter's copy
    OWNED_LOCAL = "owned_local"      # sema made it an object of its own
    REBOUND_PARAM = "rebound_param"  # a rebind into an owning parameter
    CONSUMING_LOOP = "consuming_loop"
    OWN_ELEMENT = "own_element"      # an `Own` element, loop or loop head
    COPIED_STEP = "copied_step"      # a loop / head / clause copy of a step
    OWNING_CLAUSE = "owning_clause"  # a clause that owns its elements
    COPIED_VALUE = "copied_value"    # a with target's or capture's copy

    @property
    def from_iteration(self) -> bool:
        return self in (Acquired.CONSUMING_LOOP, Acquired.OWN_ELEMENT, Acquired.COPIED_STEP,
                        Acquired.OWNING_CLAUSE)


@dataclass(frozen=True)
class BindingOwnership:
    """What one binding owns: `acquired` (None: nothing), at the paths
    `acquired_paths` (None: every leaf), less the paths a later binding
    borrows (`borrowed`, `NonOwningBind.elems`). True when it owns
    anything at all."""
    acquired: Acquired | None
    acquired_paths: frozenset[ElemPath] | None = None
    borrowed: frozenset[ElemPath] = frozenset()
    # The line of the binding that made it refer to existing storage, for
    # the copy diagnostics' note (`TypeCompatibility.ownership_note`).
    line: int | None = field(default=None, compare=False)

    def __bool__(self) -> bool:
        return self.acquired is not None

    def owned_paths(self, t: TpyType | None) -> frozenset[ElemPath]:
        """The leaf paths (`leaf_paths`) of a value of type `t` held by
        the binding that it owns: `()` for a scalar."""
        if self.acquired is None:
            return frozenset()
        leaves = leaf_paths(t)
        if self.acquired_paths is not None:
            leaves = frozenset(p for p in leaves if any(
                p[:len(a)] == a for a in self.acquired_paths))
        return frozenset(p for p in leaves if not any(
            p[:len(b)] == b for b in self.borrowed))

    def owns(self, t: TpyType | None, paths: Iterable[ElemPath]) -> bool:
        """Whether the binding owns every path in `paths` of a value of
        type `t`: each lies at or under a leaf it owns (`owned_paths`) --
        an owned leaf owns what is under it, so an owned Optional tuple
        owns its payload read at the tuple slot's paths."""
        owned = self.owned_paths(t)
        return all(any(p[:len(o)] == o for o in owned) for p in paths)

    def owned_among(self, t: TpyType | None,
                    paths: Iterable[ElemPath]) -> frozenset[ElemPath]:
        """The paths among `paths` of a value of type `t` the binding owns
        (`owns`, each on its own)."""
        owned = self.owned_paths(t)
        return frozenset(p for p in paths
                         if any(p[:len(o)] == o for o in owned))


NOT_OWNED = BindingOwnership(None)


@dataclass(frozen=True)
class BodyOwnership:
    """One body's LOCAL entries (by name; an absent name owns nothing),
    per name only loops bind each of its loop sites' entries, the locals
    holding a generator object (each owns its frame, which no use moves)
    and the locals hoisted to a slot their binds re-point."""
    locals: Mapping[str, BindingOwnership] = field(default_factory=dict)
    loop_only: Mapping[str, tuple[BindingOwnership, ...]] = field(
        default_factory=dict)
    generator_objects: frozenset[str] = frozenset()
    hoisted: frozenset[str] = frozenset()


def param_ownership(func: TpyFunction, name: str,
                    ptype: TpyType | None) -> BindingOwnership:
    """A parameter's ownership, from its declared type: what an
    ownership-transfer parameter's `Own` markers own (value leaves
    included), or the copy a by-value Optional of an expensive non-view
    payload holds (`int | None` is `std::optional<BigInt>`; a
    `str | None` is a view and owns nothing). A capture owns nothing."""
    if not isinstance(ptype, TpyType):
        return NOT_OWNED
    if func.takes_ownership_of(name, ptype):
        return BindingOwnership(Acquired.HANDED, owned_leaf_paths(ptype))
    opt = by_value_optional(ptype)
    if opt is not None and view_family_for_type(opt.inner) is None:
        return BindingOwnership(Acquired.COPIED_PARAM)
    return NOT_OWNED


def step_handover_paths(ctx: 'SemanticContext', iterable: TpyExpr,
                        step: TpyType | None, *,
                        declared: TpyType | None = None
                        ) -> frozenset[ElemPath]:
    """THE leaf paths (`leaf_paths`) of a step of iterating `iterable` -- of
    type `step` -- that the iteration hands over, read off the value's own
    type alone, whatever kind of expression holds it: the `Own` leaves of
    its declared steps (`declared_steps`; a parameter's declared
    `Iterable[Own[T]]` included, a type parameter's by its bound, and an
    `Own` its type carries by substitution,
    BUGS.md#lent-list-at-own-iterable-param-copies), or of a
    `__anext__` result (`declared`, when the caller read it); a select what
    both arms hand over, a generator expression's source what its clause
    does. A container's iteration hands over nothing. Pure, decided before
    the loop's body."""
    it_t = ctx.get_expr_type(iterable)
    if it_t is None or step is None:
        return frozenset()
    bare = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(unwrap_own(it_t))))
    if iter_element_source(bare, ctx.registry) is IterElementSource.CONTAINER:
        return frozenset()
    if declared is not None:
        return _declared_handover(declared, step)
    e = peeled_read(iterable)
    if isinstance(e, TpyIfExpr):
        return (step_handover_paths(ctx, e.then_expr, step)
                & step_handover_paths(ctx, e.else_expr, step))
    func = ctx.func.current_function
    if (isinstance(e, TpyName) and isinstance(func, TpyFunction)
            and func.is_genexpr and func.params
            and func.params[0][0] == e.name):
        return func.genexpr_step_handover
    if ctx.protocols is None:
        return frozenset()
    src_t: TpyType | None = bare
    if isinstance(bare, TypeParamRef):
        # A type parameter's value steps as its bound declares: the generic
        # hands over what its concrete twin does.
        src_t = ctx.protocols.type_ops.get_type_param_bound(bare)
        if src_t is None:
            return frozenset()
    steps = ctx.protocols.declared_steps(src_t)
    if not steps:
        # A consuming-only `__iter__` declares no lending step; what it
        # hands over is what it declares.
        own_step = builtin_modules.get_iterable_element_type(
            src_t, registry=ctx.registry, keep_own=True)
        steps = [own_step] if own_step is not None else None
    if not steps:
        return frozenset()
    # Each member C++ may select hands over only what all of them do.
    paths = _declared_handover(steps[0], step)
    for s in steps[1:]:
        paths &= _declared_handover(s, step)
    return paths


def _declared_handover(declared: TpyType, step: TpyType
                       ) -> frozenset[ElemPath]:
    """The leaf paths of `step` a declared step `declared` hands over: a
    whole `Own` every leaf, a tuple's `Own` elements their subtrees."""
    d = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(declared)))
    leaves = leaf_paths(step)
    if bare_tuple(d) is None:
        return leaves if isinstance(d, OwnType) else frozenset()
    owned = owned_leaf_paths(d)
    return frozenset(p for p in leaves
                     if any(p[:len(o)] == o for o in owned))


def owned_head_targets(head: TpyTupleUnpack) -> frozenset[str]:
    """The names a loop head or comprehension clause head binds to an
    element the step hands over (`is_owned`); of `x, x` the last target
    is the one bound."""
    last = dict(head_target_acquisitions(head))
    return frozenset(n for n, acq in last.items()
                     if acq is Acquired.OWN_ELEMENT)


def head_target_acquisitions(head: TpyTupleUnpack
                             ) -> list[tuple[str, Acquired | None]]:
    """How each target of a loop head or comprehension clause head acquires
    its element, by the one per-target record the head carries: an element
    the step hands over (`is_owned`) is the target's own, a copied one
    (`target_copies`) its copy, any other refers into the step."""
    out: list[tuple[str, Acquired | None]] = []
    for i, n in enumerate(head.targets):
        if n is None:
            continue
        out.append((n, Acquired.OWN_ELEMENT
                    if head.owns_target(i)
                    else Acquired.COPIED_STEP
                    if i < len(head.target_copies) and head.target_copies[i]
                    else None))
    return out


def loop_step_acquisition(ctx: 'SemanticContext', s: TpyForEach
                          ) -> tuple[Acquired | None, frozenset[ElemPath] | None]:
    """How loop variable `s.var` acquires its step, at which paths (None:
    every leaf): an `Own` element at the paths the iteration hands over
    (`step_handover_paths`). Read before the body is read, it is what the
    header decides: a consuming iteration is chosen after the body."""
    if s.consuming_iter_fi is not None or own_iter_source(s.iterable):
        return Acquired.CONSUMING_LOOP, None
    if s.step_handover:
        return Acquired.OWN_ELEMENT, s.step_handover
    if s.copies_var:
        return Acquired.COPIED_STEP, None
    return None, None


def _target_names(head: TpyTupleUnpack) -> tuple[str, ...]:
    """The names a loop or clause head binds, each once."""
    return tuple(dict.fromkeys(n for n in head.targets if n is not None))


def _entry(acq: Acquired | None,
           paths: frozenset[ElemPath] | None) -> BindingOwnership:
    return NOT_OWNED if acq is None else BindingOwnership(acq, paths)


_Acquisition = tuple[Acquired | None, frozenset[ElemPath] | None]


def site_acquisitions(ctx: 'SemanticContext', site: object
                      ) -> dict[str, _Acquisition] | None:
    """How each target of iteration site `site` (a `for`, a loop head
    unpack, a comprehension clause) acquires its element as the header
    decides it, at which paths (None: every leaf), by name (of `x, x` the
    last target); no acquisition where it binds module storage. None for
    a statement that is no iteration site."""
    def module(name: str, comprehension: bool = False) -> bool:
        return binds_module_storage(ctx.func, name,
                                    comprehension=comprehension)
    if isinstance(site, TpyComprehensionGenerator):
        heads = (dict(head_target_acquisitions(site.head))
                 if site.head is not None else {})
        if module(site.var, comprehension=True):
            return {n: (None, None) for n in (site.var, *heads)}
        var: _Acquisition = (
            (Acquired.OWNING_CLAUSE, site.step_handover)
            if site.step_handover
            else (Acquired.COPIED_STEP, None) if site.copies_var
            else (None, None))
        out = {site.var: var}
        out.update((n, (acq, None)) for n, acq in heads.items())
        return out
    if isinstance(site, TpyForEach):
        return {site.var: (None, None) if module(site.var)
                else loop_step_acquisition(ctx, site)}
    if isinstance(site, TpyTupleUnpack) and site.is_loop_head:
        return {n: (None, None) if module(n) else (acq, None)
                for n, acq in head_target_acquisitions(site)}
    return None


def site_acquisition(ctx: 'SemanticContext', site: object, name: str
                     ) -> _Acquisition | None:
    """`site_acquisitions` of target `name`; None for a name the site does
    not bind, or a statement that is no iteration site."""
    got = site_acquisitions(ctx, site)
    return None if got is None else got.get(name)


def header_site_ownership(ctx: 'SemanticContext', site: object,
                          name: str) -> BindingOwnership | None:
    """What loop target `name` of iteration site `site` owns as its header
    decides it (`site_acquisition`), before any read in its body. The
    settled entry (`read_ownership`) differs only by what settlement adds
    once the body is read: a binding of the name inside its loop that
    borrows (`_less_inside`) and a consuming iteration chosen after the
    body. None for any other site."""
    got = site_acquisition(ctx, site, name)
    if got is None:
        return None
    return _entry(*got)


def by_value_optional(ptype: TpyType | None) -> OptionalType | None:
    """The Optional a parameter of type `ptype` holds by value with an
    expensive payload (`int | None` is `std::optional<BigInt>`): the
    parameter's own copy, which a read may move out of. A `str | None`
    holds a view there."""
    if not isinstance(ptype, TpyType):
        return None
    u = unwrap_readonly(unwrap_send_sync(unwrap_ref_type(ptype)))
    if (isinstance(u, OptionalType) and not u.uses_pointer_repr()
            and not isinstance(ptype, ReadonlyType)
            and u.inner.is_expensive_copy()):
        return u
    return None


def receiver_ownership(func: TpyFunction) -> BindingOwnership:
    """A consuming method owns its receiver whole."""
    return (BindingOwnership(Acquired.HANDED) if func.is_consuming
            else NOT_OWNED)


def lambda_param_ownership(ptype: TpyType | None) -> BindingOwnership:
    """A lambda parameter's ownership, from the parameter type it was
    analyzed at: what an ownership-transfer type's markers own, nested
    paths included."""
    if isinstance(ptype, TpyType) and param_takes_ownership(ptype):
        return BindingOwnership(Acquired.HANDED, owned_leaf_paths(ptype))
    return NOT_OWNED


def local_ownership(ctx: 'SemanticContext', func: object,
                    name: str) -> BindingOwnership:
    """The LOCAL entry of `name` in `func`; a body sema never settled owns
    nothing."""
    body = ctx.body_ownership.get(func)
    if body is None:
        return NOT_OWNED
    return body.locals.get(name, NOT_OWNED)


def bound_name(site: object) -> str:
    """The name a declaration, a rebind or a walrus binds."""
    if isinstance(site, TpyVarDecl):
        return site.name
    if isinstance(site, TpyNamedExpr):
        return site.target
    assert isinstance(site, TpyAssign) and isinstance(site.target, TpyName)
    return site.target.name


def site_ownership(ctx: 'SemanticContext', site: object,
                   name: str) -> BindingOwnership:
    """The entry of `name` at binding site `site` (a for statement, a
    loop-head unpack, a comprehension clause, a with item, an except
    handler, a match case)."""
    entry = ctx.site_ownership.get(site)
    return NOT_OWNED if entry is None else entry.get(name, NOT_OWNED)


def generator_object(ctx: 'SemanticContext', func: object,
                     name: str) -> bool:
    """Whether local `name` of `func` holds a generator object
    (`BodyOwnership.generator_objects`): it owns its frame, which no use
    moves."""
    body = ctx.body_ownership.get(func)
    return body is not None and name in body.generator_objects


def pointer_hoisted(ctx: 'SemanticContext', func: object,
                    name: str) -> bool:
    """Whether local `name` of `func` is hoisted to a slot its binds
    re-point (`BodyOwnership.hoisted`): what it owns, no read may move out
    of in place."""
    body = ctx.body_ownership.get(func)
    return body is not None and name in body.hoisted


def frame_field_ownership(ctx: 'SemanticContext', func: object,
                          name: str) -> BindingOwnership:
    """A resumable frame field's ownership: its LOCAL entry, else -- a
    field only loops bind -- what every one of its loop sites owns
    (`_every_site_owns`)."""
    body = ctx.body_ownership.get(func)
    if body is None:
        return NOT_OWNED
    own = body.locals.get(name, NOT_OWNED)
    if own:
        return own
    sites = body.loop_only.get(name, ())
    if sites and all(sites):
        return _every_site_owns(sites)
    return NOT_OWNED


def _every_site_owns(sites: tuple[BindingOwnership, ...]
                     ) -> BindingOwnership:
    """The paths every one of the owning `sites` owns, order-free: the
    meet of their acquired paths less the union of what they borrow. Sites
    that acquired differently name the first acquisition in `Acquired`'s
    order, a fixed one."""
    acquired = min((s.acquired for s in sites),
                   key=list(Acquired).index)
    paths: frozenset[ElemPath] | None = None
    for s in sites:
        paths = _meet_paths(paths, s.acquired_paths)
    borrowed = frozenset().union(*(s.borrowed for s in sites))
    return BindingOwnership(acquired, paths, borrowed)


def _meet_paths(a: frozenset[ElemPath] | None,
                b: frozenset[ElemPath] | None) -> frozenset[ElemPath] | None:
    """The paths under both `a` and `b` (None: every leaf): of each pair
    on one branch, the deeper."""
    if a is None:
        return b
    if b is None:
        return a
    return frozenset(x if len(x) >= len(y) else y
                     for x in a for y in b
                     if x[:len(y)] == y or y[:len(x)] == x)


def binds_val_or_ref(name: str, init: TpyExpr | None,
                     vtype: TpyType | None, *, reassigned: AbstractSet[str],
                     hoisted: AbstractSet[str], move_through: AbstractSet[str],
                     frame_fields: AbstractSet[str]) -> bool:
    """Whether the declaration of open non-value `T` local `name` (of type
    `vtype`) off call `init` binds the call's `val_or_ref_t<T>` result
    itself: the callee (`callee_lend_fact`) returns its `T` from a
    non-template body, the result is no copy, and the name holds no slot
    of its own -- it is bound once, neither reassigned, hoisted, moved
    through nor a resumable frame's field. That binding is a reference
    whenever `T` is a reference type: the local borrows. A slot takes the
    result as its own. Decided once, at settlement (`val_or_ref_decls`)."""
    if not (isinstance(vtype, TypeParamRef) and not vtype.is_value_type()
            and isinstance(init, (TpyCall, TpyMethodCall))
            and init.result_form is not ResultForm.COPY
            and name not in reassigned and name not in hoisted
            and name not in move_through and name not in frame_fields):
        return False
    ops = callee_lend_fact(init).ops
    fi = ops.fi if ops is not None else None
    return (fi is not None and fi.cpp_template is None
            and isinstance(unwrap_ref_type(fi.return_type), TypeParamRef))


def read_ownership(ctx: 'SemanticContext',
                   read: ReadBinding) -> BindingOwnership:
    """What the binding a read resolves to owns (fact 1)."""
    kind, node, name = read.kind, read.node, read.name
    if kind is ReadKind.PARAM:
        ptype = next((t for p, t in node.params if p == name), None)
        return param_ownership(node, name, ptype)
    if kind is ReadKind.RECEIVER:
        return receiver_ownership(node)
    if kind is ReadKind.LAMBDA:
        i = node.param_names.index(name)
        types = list(node.inferred_param_types)
        return lambda_param_ownership(types[i] if i < len(types) else None)
    if kind is ReadKind.LOCAL:
        return local_ownership(ctx, node, name)
    if kind is ReadKind.SITE:
        return site_ownership(ctx, node, name)
    return NOT_OWNED


def _less(acq: Acquired, paths: frozenset[ElemPath] | None,
          borrowed: NonOwningBind | None) -> BindingOwnership:
    """`acq` at `paths` (None: every leaf) less what the folded
    non-owning bindings `borrowed` borrow: nothing for a whole borrow."""
    if borrowed is None:
        return BindingOwnership(acq, paths)
    if borrowed.elems is None:
        return BindingOwnership(None, None, frozenset({()}), borrowed.line)
    return BindingOwnership(acq, paths, borrowed.elems, borrowed.line)


class CompMove(Enum):
    """How a comprehension's clause decided to move a read of its variable
    (`TypeCompatibility.comp_elem_moves`): the last-evaluated sink, an
    earlier sink at its last use, the consuming receiver that is the
    variable's sole read."""
    LAST_SINK = "last_sink"
    LAST_USE = "last_use"
    RECEIVER = "receiver"


_COMPS = (TpyListComprehension, TpySetComprehension, TpyDictComprehension)


class _BodyWalk:
    """The binding sites of one body, its nested defs and generator
    expressions excluded (each settles its own)."""

    def __init__(self, ctx: 'SemanticContext',
                 fs: 'FunctionTrackingState') -> None:
        self.ctx = ctx
        self.fs = fs
        self.sites: IdentityMap = IdentityMap()
        self.non_loop: set[str] = set()
        self.lending: set[str] = set()
        func = fs.current_function
        frame = (func.generator_locals or ()) if isinstance(
            func, TpyFunction) else ()
        self.slots = dict(reassigned=fs.current_reassigned_vars,
                          hoisted=fs.hoisted_vars,
                          move_through=fs.move_through_vars,
                          frame_fields={n for n, _t in frame})
        # The for statements each node of the body lies in, innermost last.
        self.loops: tuple[TpyForEach, ...] = ()
        self.within: IdentityMap = IdentityMap()
        # The iteration sites, resolved once the whole body is read: the
        # site node, the name, its acquisition at its paths, its loop, and
        # whether the loop binds the name itself (a frame field only loops
        # bind).
        self.pending: list[tuple[object, str, Acquired | None,
                                 frozenset[ElemPath] | None,
                                 TpyForEach, bool]] = []
        self.params = (frozenset(p for p, _t in func.params)
                       if isinstance(func, TpyFunction) else frozenset())
        # Each name binding's non-owning notes, by the value it binds.
        self.notes_at: IdentityMap = IdentityMap()
        for n, entry in fs.nonowning_sites.items():
            for at, elems, _source in entry.notes:
                if isinstance(at, TpyExpr):
                    self.notes_at.setdefault(peel_value_wrappers(at),
                                             []).append((n, elems))
        # The name-binding sites of each local, in walk order.
        self.bind_sites: dict[str, list[object]] = {}

    def stmts(self, stmts: list[TpyStmt]) -> None:
        for s in stmts:
            self.within[s] = self.loops
            self.stmt(s)
            if isinstance(s, TpyNestedDef):
                continue
            for e in s.exprs():
                walk_expr_tree(e, self.expr)
            outer = self.loops
            for body in s.sub_bodies():
                # A loop's `else` block is no pass of it.
                self.loops = (outer + (s,) if isinstance(s, TpyForEach)
                              and body is s.body else outer)
                self.stmts(body)
            self.loops = outer

    def stmt(self, s: TpyStmt) -> None:
        bound = written_names(s)
        if isinstance(s, TpyForEach):
            bound.discard(s.var)
            acq, paths = self._acquisitions(s)[s.var]
            self.pending.append((s, s.var, acq, paths, s,
                                 not s.is_tuple_unpack))
        elif isinstance(s, TpyTupleUnpack) and s.is_loop_head:
            for n, (acq, paths) in self._acquisitions(s).items():
                bound.discard(n)
                self.pending.append((s, n, acq, paths, self.loops[-1],
                                     True))
        elif isinstance(s, TpyWith):
            # A value enter result's `auto` copy is the target's own; any
            # other target refers to the manager's storage.
            for item in s.items:
                if item.target is not None:
                    et = item.enter_type
                    self.sites[item] = {item.target: BindingOwnership(
                        Acquired.COPIED_VALUE) if isinstance(et, TpyType)
                        and et.is_value_type()
                        and not self._module(item.target) else NOT_OWNED}
        elif isinstance(s, TpyTry):
            # A reference to the exception in flight.
            for h in s.handlers:
                if h.binding is not None:
                    self.sites[h] = {h.binding: NOT_OWNED}
        elif isinstance(s, TpyMatch):
            for case in s.cases:
                self.sites[case] = {
                    c.name: BindingOwnership(Acquired.COPIED_VALUE)
                    if c.bind_by_value and not self._module(c.name)
                    else NOT_OWNED
                    for c in iter_capture_bindings(case.pattern)}
        elif isinstance(s, TpyVarDecl) and s.init is not None:
            lending = binds_val_or_ref(
                s.name, s.init, declared_bound_type(
                    s, self.ctx.var_types, self.ctx.get_expr_type(s.init)),
                **self.slots)
            if lending:
                self.lending.add(s.name)
                self.ctx.val_or_ref_decls.add(s)
            self._bind_site(s, s.name, s.init, lending=lending)
        elif isinstance(s, TpyAssign) and isinstance(s.target, TpyName):
            self._bind_site(s, s.target.name, s.value)
        self.non_loop |= bound

    def _acquisitions(self, site: object) -> dict[str, _Acquisition]:
        got = site_acquisitions(self.ctx, site)
        assert got is not None, ("no iteration site", site)
        return got

    def _bind_site(self, site: object, name: str, value: TpyExpr, *,
                   lending: bool = False) -> None:
        """The entry of a name binding -- a declaration, a rebind, a walrus
        -- at its own site: what the value it binds hands it, less what the
        binding's own non-owning notes borrow (`fold_nonowning`). A
        declaration binding a `val_or_ref_t` result itself owns nothing;
        module storage owns nothing (a global is a slot a function still
        reads, unless its layout holds values). A parameter's ownership is
        its type's: no site."""
        if name in self.params:
            return
        if lending:
            self.sites[site] = {name: NOT_OWNED}
            return
        if self._module(name):
            # Module storage owns by its layout: a pointer slot aliases, a
            # value global holds what it is bound to.
            gt = self.ctx.global_scope.lookup(name)
            if gt is None or global_binds_by_reference(gt):
                self.sites[site] = {name: NOT_OWNED}
                return
        rec: NonOwningBind | None = None
        for n, elems in self.notes_at.get(peel_value_wrappers(value), ()):
            if n == name:
                rec = fold_nonowning(rec, value, elems)
        self.sites[site] = {name: _less(Acquired.OWNED_LOCAL, None, rec)}
        if not self._module(name):
            self.bind_sites.setdefault(name, []).append(site)

    def _module(self, name: str, *, comprehension: bool = False) -> bool:
        """Whether target `name` -- a statement's, or a comprehension
        clause's (`comprehension`) -- binds module storage."""
        return binds_module_storage(self.fs, name,
                                    comprehension=comprehension)

    def resolve_sites(self) -> dict[str, list[BindingOwnership]]:
        """Each iteration site's entry: its acquisition, less what the
        bindings of its name inside its own loop borrow (a binding of the
        name outside the loop is another binding's). Returns the entries
        of the names each loop binds itself, by name."""
        loop_bound: dict[str, list[BindingOwnership]] = {}
        for node, name, acq, paths, loop, binds in self.pending:
            own = (NOT_OWNED if acq is None or self._module(name)
                   else self._less_inside(name, acq, paths, loop))
            self.sites.setdefault(node, {})[name] = own
            if binds:
                loop_bound.setdefault(name, []).append(own)
        return loop_bound

    def _less_inside(self, name: str, acq: Acquired,
                     paths: frozenset[ElemPath] | None,
                     loop: TpyForEach) -> BindingOwnership:
        """The site's entry: `acq` at `paths` (None: every leaf), less what
        the bindings of `name` inside
        `loop` borrow (a binding borrowing it whole owns nothing), with the
        line of the first of them."""
        inside: NonOwningBind | None = None
        entry = self.fs.nonowning_sites.get(name)
        for at, elems, _source in entry.notes if entry is not None else ():
            within = self.within.get(at, ()) if at is not None else ()
            if any(loop is lp for lp in within):
                inside = fold_nonowning(inside, at, elems)
        return _less(acq, paths, inside)

    def expr(self, e: TpyExpr) -> bool:
        self.within[e] = self.loops
        if isinstance(e, TpyNamedExpr):
            # A rebinding walrus is its binding statement's site.
            self._bind_site(e.binding if e.binding is not None else e,
                            e.target, e.value)
        if isinstance(e, TpyGeneratorExpression):
            # A function of its own: only its first source is this body's.
            if e.generators:
                walk_expr_tree(e.generators[0].iterable, self.expr)
            return False
        if isinstance(e, _COMPS):
            for gen in e.generators:
                heads = _target_names(gen.head) if gen.head is not None else ()
                out = {n: _entry(*got)
                       for n, got in self._acquisitions(gen).items()}
                if gen.head is not None:
                    # The head's targets, keyed by the clause (what its
                    # reads resolve to) and by the head (its planner site).
                    self.sites[gen.head] = {n: out[n] for n in heads}
                self.sites[gen] = out
        return True


def _sites_meet(walk: _BodyWalk, name: str,
                entry: BindingOwnership) -> BindingOwnership:
    """A LOCAL's entry: the meet of its name-binding sites' entries
    (`_every_site_owns`), less what its other bindings to existing
    storage borrow (an unpack target, a `with` target)."""
    sites = [walk.sites[x][name] for x in walk.bind_sites.get(name, ())]
    if not sites:
        # Bound by no declaration, rebind or walrus (an unpack target).
        return entry
    if not all(sites):
        return NOT_OWNED
    meet = _every_site_owns(tuple(sites))
    values = IdentitySet()
    for x in walk.bind_sites[name]:
        values.add(peel_value_wrappers(
            x.init if isinstance(x, TpyVarDecl) else x.value))
    rest: NonOwningBind | None = None
    entry = walk.fs.nonowning_sites.get(name)
    for at, elems, _source in entry.notes if entry is not None else ():
        if not (isinstance(at, TpyExpr)
                and peel_value_wrappers(at) in values):
            rest = fold_nonowning(rest, at, elems)
    if rest is None:
        return meet
    if rest.elems is None:
        return NOT_OWNED
    return BindingOwnership(meet.acquired, meet.acquired_paths,
                            meet.borrowed | rest.elems)


def record_body_ownership(ctx: 'SemanticContext',
                          body: list[TpyStmt]) -> None:
    """THE writer of fact 1 for the body being settled (`ctx.func`): the
    entries of its binding sites (iteration binders, `with` items, `except`
    handlers, `match` cases) and, for a function, of its locals, its
    generator objects and its pointer-hoisted locals. A module's top level
    owns no local: its names are module storage."""
    fs = ctx.func
    walk = _BodyWalk(ctx, fs)
    walk.stmts(body)
    loop_bound = walk.resolve_sites()
    for site, entry in walk.sites.items():
        ctx.site_ownership[site] = entry
    func = fs.current_function
    if not ctx.in_function_body():
        return
    owning_params = {p for p, t in func.params
                     if p in fs.current_rebound_params
                     and func.takes_ownership_of(p, t)}
    locals_: dict[str, BindingOwnership] = {}
    for name in (fs.ever_owned_locals | fs.owned_generator_locals
                 | owning_params):
        if (name in walk.lending or not fs.owns_whole(name)
                or binds_module_storage(fs, name, comprehension=False)):
            continue
        bind = fs.nonowning_bind(name)
        entry = BindingOwnership(
            Acquired.REBOUND_PARAM if name in owning_params
            and name not in fs.ever_owned_locals else Acquired.OWNED_LOCAL,
            None, bind.elems if bind is not None else frozenset())
        if entry.acquired is Acquired.OWNED_LOCAL:
            entry = _sites_meet(walk, name, entry)
        if entry:
            locals_[name] = entry
    loop_only = {n: tuple(sites) for n, sites in loop_bound.items()
                 if n not in walk.non_loop}
    ctx.body_ownership[func] = BodyOwnership(
        locals_, loop_only, frozenset(fs.owned_generator_locals),
        frozenset(fs.hoisted_vars))
