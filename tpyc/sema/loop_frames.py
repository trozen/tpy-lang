"""A generator or coroutine held across a loop's back edge.

In a generator or async body a frame-holding local is a frame field, open
until its name is bound again, deleted or the frame finishes -- CPython's
lifetime. One first bound in a loop body is therefore still open, from the
previous pass, when the next pass runs. Where that pass binds a name the
old frame borrows, CPython binds a new object and the old one lives on in
the frame, while TPy writes the one storage both passes share: the old
frame then reads the new value, or freed memory, before or while it
closes. An in-place write is shared under CPython too, but may move what
the old frame points into (a growing list), so it counts as well -- unless
it moves nothing: a store to a plain field holding a free-copy scalar.

So such a binding is an error when the loop may write anything it borrows.
"Held" comes from the alias-rebind replay (a frame closed by `del`, run to
its end by `await` or never reaching the back edge is not held); "may
write" is `prescan.LoopBindings` read against the frame's storage closure:

* the roots of the frame (`FunctionTrackingState.frame_local_roots`) and
  every storage they borrow from (upstream) -- a whole-name binding of one
  of these, in the loop head or body, writes it;
* those and every name borrowing from them (downstream, frame-holding
  locals excepted -- their writes count at the call that built them) --
  a store into a place under one, an iteration advancing one, or a call
  that may mutate one writes it -- except the call that builds the held
  frame's next value: binding it closes the previous frame before the new
  one runs;
* a loop variable stepped through a container named by a field
  (`for row in self.rows`) is not written by the step: the container is,
  by a store under its field chain, a binding of its root or a call that
  may mutate the root.

A for-each variable reaches into what it iterates. A call counts with the
ones the loop makes unspelled: every dunder sema records as a receiver call
(`receiver_calls.implicit_calls`: an element read, store or `del`, `in`, an
iteration, a truth test, and a runtime callee's structural protocol
parameter's methods, such as `len()`'s `__len__`), a `with` manager's enter
and exit, an augmented assignment's
in-place dunder, a nested def (through every capture), and running a
frame-holding local built outside the loop (its building calls).

Calls are decided once every module's mutation facts are final (unknown
means writes: a callee without facts, a callable value, and -- while no
per-function global-write fact exists -- any bodied callee when a root is a
module global).

A `with` manager's exit writes under the same rule, read over the exit's
body with its receiver standing for the manager: in a generator or async
body a generator bound in the block may not stay open past the block when
it borrows storage the exit may write (`WithExitCheck`). (A plain function
keeps such a generator in the block's C++ scope unless a read after the
block declares it in front of the block, and then it may borrow only
storage that outlives the body.)

The same walk decides a second fact, whether
the loop may write the storage its variable's element lives in while the
variable is live (`note_source_writes`). It is a CLOSED proof
(`_SourceProof`): the region keeps the variable's reference only when
every statement and expression is of a small closed set of kinds, every
value it touches runs no user code (`TpyType.value_ops_run_user_code`) and
no write reaches a holding type; anything else copies. It runs at body
time, so a callee with a body may write anything; a write rooted at a
function local that owns its object is decided at the end of the body over
the same storage closure (`resolve_source_writes`). Both questions
decompose a call the same way (`call_parts`, `call_write`). A loop whose
element names a type parameter keeps its reference
(BUGS.md#generic-loop-var-sees-source-write).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum, auto
from typing import TYPE_CHECKING, AbstractSet, Callable, Iterable

from ..parse import (
    TpyAugAssign, TpyAwait, TpyBinOp, TpyCall, TpyCoerce, TpyFieldAccess,
    TpyForEach, TpyMethodCall, TpyName, TpyStmt, TpySubscript,
    TpyTupleUnpack, TpyUnaryOp, TpyVarargPack, TpyVarDecl, TpyStarUnpack,
    TpyWith,
)
from ..parse.nodes import (
    TpyArrayLiteral, TpyBytesLiteral, TpyDictComprehension, TpyDictLiteral,
    TpyFunction, TpyGeneratorExpression, TpyListComprehension, TpyListRepeat,
    TpySetComprehension, TpySetLiteral, TpySlice, TpyStrLiteral,
    TpyTupleLiteral, is_property_getter_read, walk_body_stmts,
)
from ..compilation_context import get_current_compiler
from ..prescan import block_writes, loop_bindings_of
from ..type_def_registry import is_free_copy_scalar
from ..typesys import (
    ReadonlyType, TpyType, is_bodyless_binding, is_declaration_only,
    is_indirection_type, type_may_hold, unwrap_own, unwrap_qualifiers,
    unwrap_readonly, unwrap_ref_type,
)
from ..value_category import call_returns_cpp_ref
from .iter_loans import iterated_storage
from .own_copy import holds_only_values
from .receiver_calls import ImplicitCall, implicit_calls
from .context import (
    ITER_BORROWER, _root_name_of_expr, _stable_loop_source, _storage_root,
    call_param_args, field_chain_storage_key, frame_binding_calls,
    may_hold_pointer,
)
from .may_interrupt import InertWalk, NotInert, is_inert_type

if TYPE_CHECKING:
    from ..parse import TpyComprehensionGenerator, TpyExpr, TpyWithItem
    from ..typesys import FunctionInfo
    from .context import SemanticContext


@dataclass
class LoopFrameHold:
    """A frame-holding local still open at a loop's back edge, with the
    storage a write to which it would see: `upstream` (a whole-name
    binding writes it) and `reach` (a store or mutating call through it
    does). `globals_` are the module globals among `upstream`."""
    holder: str
    loop: TpyStmt
    upstream: frozenset[str]
    reach: frozenset[str]
    globals_: frozenset[str]
    node: 'TpyExpr | TpyStmt'
    # The calls that bind `holder` itself: building its next frame runs
    # nothing before the binding closes the previous one.
    own_calls: tuple = ()


@dataclass
class PendingLoopFrameCall:
    """A call a loop makes while `hold` is open, decided once mutation
    facts are final."""
    hold: LoopFrameHold
    call: 'TpyExpr | ImplicitCall'


@dataclass(frozen=True)
class StorageEdges:
    """A body's loan graph: which names each name lends to and borrows
    from, and the for-each variables over something untraceable."""
    lends: 'dict[str, set[str]]'
    borrows: 'dict[str, set[str]]'
    untraced: frozenset[str]


def storage_edges(stmt_loans,
                  for_edges: 'Iterable[tuple[set[str], str | None]]' = ()
                  ) -> StorageEdges:
    """Every loan the borrow tracker registered in the body --
    flow-insensitive on purpose: an alias taken anywhere may be the one a
    write goes through. A for-each variable reaches into what it iterates
    (`for_edges`) but is not upstream of it: the tracker files no loan for
    it; one over something untraceable may refer into anything. Built once
    per body (`storage_closure` reads it per question)."""
    lends: dict[str, set[str]] = {}
    borrows: dict[str, set[str]] = {}
    for entries in stmt_loans.values():
        for storage, holder, _loan in entries:
            if holder.startswith(ITER_BORROWER):
                continue
            src = storage.split(".", 1)[0]
            if src == holder:
                continue
            borrows.setdefault(holder, set()).add(src)
            lends.setdefault(src, set()).add(holder)
    untraced: set[str] = set()
    for names, src in for_edges:
        if src is not None:
            lends.setdefault(src, set()).update(names - {src})
        else:
            untraced |= names
    return StorageEdges(lends, borrows, frozenset(untraced))


def storage_closure(edges: StorageEdges, frames: 'frozenset[str]',
                    roots: 'set[str]') -> 'tuple[set[str], set[str]]':
    """`roots` with every storage they borrow from (upstream), and that
    with every name borrowing from it (reach), over the body's loan graph;
    a for-each variable over something untraceable is in every reach."""

    def reach_from(start: 'set[str]', step: 'dict[str, set[str]]',
                   skip: 'frozenset[str]') -> set[str]:
        out = set(start)
        todo = list(start)
        while todo:
            n = todo.pop()
            for m in step.get(n, ()):
                if m not in out and m not in skip:
                    out.add(m)
                    todo.append(m)
        return out

    upstream = reach_from(roots, edges.borrows, frozenset())
    reach = reach_from(upstream | (edges.untraced if roots else set()),
                       edges.lends, frames)
    return upstream, reach


def check_loop_hold(ctx: 'SemanticContext', hold: LoopFrameHold) -> None:
    """Reject `hold` when its loop writes what it borrows by a binding, a
    store, an iteration or a `with`; queue the loop's calls for the check
    after mutation propagation."""
    facts = loop_bindings_of(ctx.loop_bindings, hold.loop,
                             ctx.write_summaries)
    rebinds = facts.body | facts.target
    container = _stepped_container(ctx, hold, facts.body)
    if container is not None:
        # The loop points its variable at the next element, which leaves
        # the element the old frame borrows where it is: what the frame
        # needs to stay put is the container.
        rebinds = facts.body
    upstream = hold.upstream | (
        {container.split(".", 1)[0]} if container is not None else set())
    rebound = sorted(rebinds & upstream - {hold.holder}, key=_user_first)
    if rebound:
        raise _error(ctx, hold, f"the loop binds {_shown(rebound[0])} again")
    # A callee may replace the container through its root.
    call_hold = hold if container is None else replace(
        hold, reach=hold.reach | upstream)

    def queue(call: 'TpyExpr | ImplicitCall') -> None:
        ctx.pending_loop_frame_calls.append(
            PendingLoopFrameCall(call_hold, call))

    for place in facts.stores:
        setter = getattr(_peel(place), "property_setter_call", None)
        if setter is not None:
            queue(setter)
        root = _root_name_of_expr(place)
        if root is not None and (root in hold.reach or (
                container is not None
                and _overlaps(_place_key(place), container, root))):
            if not any(place is d for d in facts.deletes) and _store_in_place(
                    ctx, place):
                continue
            raise _error(ctx, hold, f"the loop stores into '{root}'")
    # Any use of a generator or coroutine the loop did not build may run it.
    for name in sorted(_names_used(hold.loop) - facts.body - facts.target):
        _queue_frame_run(ctx, hold, name, queue)
    for e in facts.effects:
        if any(e is c for c in hold.own_calls):
            continue
        if isinstance(e, TpyForEach):
            root = _root_name_of_expr(_peel(e.iterable))
            if (root is not None and root in hold.reach
                    and not _stable_loop_source(ctx, e)):
                raise _error(ctx, hold, f"the loop advances '{root}'")
        elif isinstance(e, TpyWith):
            for item in e.items:
                root = _root_name_of_expr(_peel(item.context_expr))
                if root is not None and root in hold.reach:
                    raise _error(ctx, hold,
                                 f"the loop enters a 'with' on '{root}'")
                methods = manager_methods(ctx, item, e.is_async)
                if methods is None:
                    raise _error(ctx, hold, "the loop enters a 'with' whose "
                                 "writes are not known")
                for fi in methods:
                    queue(ImplicitCall(fi, item.context_expr, fi.name))
        elif isinstance(e, TpyAwait):
            continue
        else:
            why = _nested_def_write(ctx, call_hold, e)
            if why is not None:
                raise _error(ctx, hold, why)
            queue(e)
    # The dunders it runs unspelled: an element read or store, `in`, an
    # iteration.
    for site in facts.sites:
        for c in implicit_calls(site):
            queue(c)


def manager_methods(ctx: 'SemanticContext', item: 'TpyWithItem',
                    is_async: bool) -> 'list[FunctionInfo] | None':
    """The enter and exit methods a `with` item runs, or None when the
    manager's type does not name them."""
    t = ctx.get_expr_type(item.context_expr)
    if t is None:
        return None
    record = ctx.registry.get_record_for_type(
        unwrap_own(unwrap_readonly(unwrap_ref_type(t))))
    if record is None:
        return None
    out: 'list[FunctionInfo]' = []
    for name in (("__aenter__", "__aexit__") if is_async
                 else ("__enter__", "__exit__")):
        overloads = ctx.registry.get_method_overloads_with_parents(
            record, name)
        if not overloads:
            return None
        out += overloads
    return out


@dataclass
class WithExitCheck:
    """A generator bound in a generator or async body's `with` block and
    still open when the block exits, over the storage it borrows; decided
    once mutation facts are final."""
    stmt: TpyWith
    holder: str
    reach: frozenset[str]
    globals_: frozenset[str]
    node: 'TpyExpr | TpyStmt'


def _resolve_with_exit(ctx: 'SemanticContext', chk: WithExitCheck) -> None:
    """A generator a manager's exit may write under must close before the
    exit."""
    why = with_exit_write(ctx, chk.stmt, chk.reach, chk.globals_)
    if why is None:
        return
    g = chk.holder
    raise ctx.error(
        f"cannot keep '{g}' open past the 'with' block it is bound in: "
        f"the block's exit may write {why}, which '{g}' borrows; close "
        f"it with 'del {g}' before the block ends, or iterate it inside a "
        f"helper function", chk.node)


def with_exit_write(ctx: 'SemanticContext', stmt: TpyWith,
                    reach: 'AbstractSet[str]',
                    globals_: 'AbstractSet[str]') -> str | None:
    """What the exit of one of `stmt`'s managers may write among the
    storage `reach` names or the module globals `globals_` -- quoted for a
    diagnostic -- or None: the loop rule read over the exit's body, its
    receiver standing for the manager."""
    for item in stmt.items:
        root = _manager_root(item.context_expr)
        under = (root in reach if root is not None
                 else item.manager_borrowed and bool(reach))
        if not under and not globals_:
            continue
        label = f"'{root}'" if root is not None else "its manager's storage"
        methods = _exit_methods(ctx, item, stmt.is_async)
        if methods is None:
            return label if under else _global_label(globals_)
        for fi in methods:
            hit = _exit_body_write(ctx, fi.root, under, globals_)
            if hit is not None:
                return label if hit == "" else hit
    return None


def _exit_call_write(ctx: 'SemanticContext', fi: 'FunctionInfo', under: bool,
                     globals_: 'AbstractSet[str]') -> str | None:
    """An exit whose body is not at hand, read as the call a loop makes to
    it: its receiver facts, and the module-global rule."""
    recv = TpyName(name="__manager__")
    reach = frozenset({"__manager__"} if under else ()) | frozenset(globals_)
    hold = LoopFrameHold(holder="", loop=None, upstream=reach, reach=reach,
                         globals_=frozenset(globals_), node=recv)
    hit = _call_write(ctx, hold, ImplicitCall(fi, recv, fi.name))
    if hit is None:
        return None
    if hit[1] == "__manager__" or hit[1] is None and under:
        return ""
    return _global_label(globals_)


def _global_label(globals_: 'AbstractSet[str]') -> str:
    return f"the module global '{sorted(globals_)[0]}'"


def _exit_body_write(ctx: 'SemanticContext', fi: 'FunctionInfo',
                     under: bool, globals_: 'AbstractSet[str]') -> str | None:
    """"" when exit method `fi` may write its receiver (`under`: the storage
    a generator borrows is under it), a module-global label when it may
    write one of `globals_`, else None. Its body's stores, iterations and
    calls count as a loop body's do; a name it binds itself may alias the
    receiver, so writes through one count too."""
    if fi.is_pure or fi.is_readonly:
        return None
    compiler = get_current_compiler()
    decl = compiler.single_method_body(fi.owning_type_qname, fi.name) if compiler is not None else None
    if decl is None or is_bodyless_binding(fi):
        return _exit_call_write(ctx, fi, under, globals_)
    self_name = "self"
    facts = block_writes(decl.body)
    locals_ = ({self_name} | facts.body) if under else set()
    reach = frozenset(locals_ | set(globals_))
    hold = LoopFrameHold(holder="", loop=decl, upstream=reach, reach=reach,
                         globals_=frozenset(globals_), node=decl)

    def label(root: str | None) -> str:
        if root is not None and root in globals_ and root not in locals_:
            return f"the module global '{root}'"
        return "" if under else _global_label(globals_)

    rebound = sorted(facts.body & set(globals_))
    if rebound:
        return label(rebound[0])
    for place in facts.stores:
        root = _root_name_of_expr(place)
        if root in reach and (any(place is d for d in facts.deletes)
                              or not _store_in_place(ctx, place)):
            return label(root)
    for e in facts.effects:
        if isinstance(e, TpyForEach):
            root = _root_name_of_expr(_peel(e.iterable))
            if root in reach and not _stable_loop_source(ctx, e):
                return label(root)
            continue
        if isinstance(e, (TpyWith, TpyAwait)):
            return label(None)
        hit = _call_write(ctx, hold, e)
        if hit is not None:
            return label(hit[1])
    for site in facts.sites:
        for c in implicit_calls(site):
            hit = _call_write(ctx, hold, c)
            if hit is not None:
                return label(hit[1])
    return None


def _exit_methods(ctx: 'SemanticContext', item: 'TpyWithItem',
                  is_async: bool) -> 'list[FunctionInfo] | None':
    methods = manager_methods(ctx, item, is_async)
    if methods is None:
        return None
    exit_name = "__aexit__" if is_async else "__exit__"
    return [fi for fi in methods if fi.name == exit_name]


def _manager_root(e: 'TpyExpr') -> str | None:
    """The name a manager expression's storage is reached through: the
    receiver chain under a field, element or method call."""
    e = _peel(e)
    while isinstance(e, (TpyFieldAccess, TpySubscript, TpyMethodCall)):
        e = _peel(e.obj)
    return e.name if isinstance(e, TpyName) else None


def _queue_frame_run(ctx: 'SemanticContext', hold: LoopFrameHold,
                     name: str, queue) -> None:
    """A loop that uses another frame-holding local may run its body (by
    iterating it, awaiting it, pulling it through a method, handing it to
    a call), which writes what the calls that built it may write -- built
    outside the loop, those are not among the loop's own calls."""
    if name == hold.holder or name not in ctx.func.frame_local_roots:
        return
    for node in ctx.func.frame_binding_nodes.get(name) or [None]:
        calls = (frame_binding_calls(binding_value(node))
                 if node is not None else None)
        if calls is None:
            raise _error(ctx, hold, f"the loop runs '{name}', whose "
                         f"writes are not known")
        for c in calls:
            queue(c)


def _names_used(stmt: 'TpyStmt') -> set[str]:
    """Every name `stmt` reads or writes, nested blocks and lambdas
    included."""
    out: set[str] = set()

    def expr(e) -> None:
        if isinstance(e, TpyName):
            out.add(e.name)
        # TODO: walk through parse.nodes.walk_expr_tree (the shared pruning visitor) instead of an own children() loop.
        for c in e.children():
            if c is not None:
                expr(c)

    def stmts(ss) -> None:
        for s in ss:
            for e in s.exprs():
                if e is not None:
                    expr(e)
            for sub in s.sub_bodies():
                stmts(sub)

    stmts([stmt])
    return out


def _call_args(e: 'TpyExpr') -> 'list[TpyExpr]':
    if isinstance(e, (TpyCall, TpyMethodCall)):
        return [a for arg in e.args for a in _arg_exprs(arg)] + list(
            (e.kwargs or {}).values())
    return []


def _nested_def_write(ctx: 'SemanticContext', hold: LoopFrameHold,
                      call: 'TpyExpr') -> str | None:
    """Why a call that runs a nested def -- called, or handed to a callee
    -- may write what `hold` borrows: through its captures (every capture
    counts, the def's own writes to them are not kept), or a module global
    it may write."""
    names: 'list[TpyExpr]' = list(_call_args(call))
    if isinstance(call, TpyCall):
        names.append(call.func)
    for n in names:
        n = _peel(n)
        if not (isinstance(n, TpyName)
                and n.name in ctx.func.nested_def_names):
            continue
        node = ctx.func.nested_def_nodes.get(n.name)
        if node is None:
            return f"the loop calls '{n.name}', whose writes are not known"
        hit = sorted((set(node.captured_names) | node.nonlocal_names)
                     & hold.reach, key=_user_first)
        if hit:
            return f"the loop calls '{n.name}', which may write {_shown(hit[0])}"
        if hold.globals_:
            g = sorted(hold.globals_)[0]
            return (f"the loop calls '{n.name}', which may write the module "
                    f"global '{g}'")
    return None


def binding_value(node: 'TpyExpr | TpyStmt') -> 'TpyExpr | None':
    """The value one frame binding node binds: a declaration's init, an
    assignment's or a walrus's value."""
    if isinstance(node, TpyVarDecl):
        return node.init
    return getattr(node, "value", None)


def _stepped_container(ctx: 'SemanticContext', hold: LoopFrameHold,
                       body: 'frozenset[str]') -> str | None:
    """The field chain (`self.rows`) of the container `hold`'s loop steps
    its own variable through, when the frame borrows that variable. (A
    container named by a plain name is the frame's root already:
    `_through_stable_loop_vars`.)"""
    loop = hold.loop
    if (not isinstance(loop, TpyForEach) or loop.var not in hold.upstream
            or loop.var in body or not _stable_loop_source(ctx, loop)):
        return None
    return field_chain_storage_key(_peel(loop.iterable))


def _is_free_copy_scalar_expr(ctx: 'SemanticContext', e: 'TpyExpr') -> bool:
    t = ctx.get_expr_type(_peel(e))
    return t is not None and is_free_copy_scalar(
        unwrap_readonly(unwrap_ref_type(t)))


def _store_in_place(ctx: 'SemanticContext', place: 'TpyExpr') -> bool:
    """A store that writes `place` where it is, moving nothing a frame can
    point into: a plain field holding a free-copy scalar. (An element store
    counts: the frame may hold a reference to the element, and a dict
    store may insert.)"""
    place = _peel(place)
    if (not isinstance(place, TpyFieldAccess)
            or place.property_setter_call is not None):
        return False
    return _is_free_copy_scalar_expr(ctx, place)


def _place_key(place: 'TpyExpr') -> str | None:
    """The field chain a store lands in: an element store writes the
    container the chain names."""
    place = _peel(place)
    while isinstance(place, TpySubscript):
        place = _peel(place.obj)
    return field_chain_storage_key(place)


def _overlaps(key: str | None, container: str, root: str) -> bool:
    """A store at `key` may write `container`: one is a prefix of the
    other, or the store's chain is unknown under the container's root."""
    if key is None:
        return root == container.split(".", 1)[0]
    return (key == container or key.startswith(container + ".")
            or container.startswith(key + "."))


def resolve_loop_frame_calls(ctx: 'SemanticContext') -> None:
    """The call half of `check_loop_hold`, and the `with` exit checks,
    with final mutation facts."""
    pending = ctx.pending_loop_frame_calls
    ctx.pending_loop_frame_calls = []
    for entry in pending:
        hit = _call_write(ctx, entry.hold, entry.call)
        if hit is not None:
            raise _error(ctx, entry.hold, _call_why(hit))
    checks = ctx.pending_with_exit_checks
    ctx.pending_with_exit_checks = []
    for chk in checks:
        _resolve_with_exit(ctx, chk)


def _call_why(hit: 'tuple[str, str | None, bool]') -> str:
    name, root, global_rule = hit
    if root is None:
        return f"the loop calls '{name}', whose writes are not known"
    if global_rule:
        return (f"the loop calls '{name}', which may write the module "
                f"global '{root}'")
    return f"the loop calls '{name}', which may write '{root}'"


def _call_write(ctx: 'SemanticContext', hold: LoopFrameHold,
                call: 'TpyExpr | ImplicitCall'
                ) -> 'tuple[str, str | None, bool] | None':
    """The callee's name and the root in `hold`'s reach `call` may write
    (None: its writes are not known), with whether only the module-global
    rule says so; None when it writes nothing `hold` borrows."""
    parts = call_parts(ctx, call)
    if parts is None:
        return None

    def in_reach(e: 'TpyExpr') -> 'str | None':
        root = _root_name_of_expr(_peel(e))
        return root if root is not None and root in hold.reach else None

    hit = call_write(ctx, parts, in_reach, facts_final=True,
                     globals_written=bool(hold.globals_))
    if hit is None:
        return None
    kind, label = hit
    if kind is CallWrite.GLOBAL:
        return parts.name, sorted(hold.globals_)[0], True
    if kind is CallWrite.OPERAND:
        return parts.name, label, False
    return parts.name, None, False


@dataclass(frozen=True)
class CallParts:
    """One call a site makes, explicit or not: how it is named in a
    diagnostic, the callee (None when no FunctionInfo names it), its
    receiver and its (parameter index, argument) pairs."""
    name: str
    fi: 'FunctionInfo | None'
    receiver: 'TpyExpr | None' = None
    args: 'tuple[tuple[int, TpyExpr], ...]' = ()


def call_parts(ctx: 'SemanticContext', call: 'TpyExpr | ImplicitCall'
               ) -> 'CallParts | None':
    """The call `call` makes -- an explicit call, an implicit dunder
    (`implicit_calls`), an augmented assignment's in-place operator, a
    binary or unary operator dunder, a `__contains__` -- or None when it
    makes none (an operator on scalars, a scalar augmented store)."""
    if isinstance(call, ImplicitCall):
        if call.fi is None:
            return CallParts(call.method, None)
        return CallParts(call.fi.name, call.fi, call.receiver)
    if isinstance(call, TpyAugAssign):
        # On a scalar the in-place operator is the store itself, which the
        # stores decide.
        if call.resolved_inplace is None or _is_free_copy_scalar_expr(
                ctx, call.target):
            return None
        fi = call.resolved_inplace.method.root
        return CallParts(fi.name, fi, call.target, ((0, call.value),))
    if isinstance(call, (TpyCall, TpyMethodCall)):
        fi = call.resolved_function_info
        name = (call.method if isinstance(call, TpyMethodCall)
                else call.maybe_func_name or "a callable")
        if fi is None or fi.is_callable_value:
            return CallParts(name, None)
        receiver = (call.obj if isinstance(call, TpyMethodCall)
                    and not call.is_static_call
                    and not call.user_module_call else None)
        index = {id(p): i for i, p in enumerate(fi.params)}
        return CallParts(name, fi, receiver, tuple(
            (index[id(p)], a) for p, a in call_param_args(call)))
    if isinstance(call, TpyBinOp) and call.resolved_binop is not None:
        rb = call.resolved_binop
        fi = rb.method.root
        return CallParts(fi.name, fi,
                         call.right if rb.is_reverse else call.left,
                         ((0, call.left if rb.is_reverse else call.right),))
    if isinstance(call, TpyBinOp) and call.resolved_contains is not None:
        fi = call.resolved_contains
        return CallParts(fi.name, fi, call.right, ((0, call.left),))
    if isinstance(call, TpyUnaryOp) and call.resolved_unaryop is not None:
        fi = call.resolved_unaryop.method.root
        return CallParts(fi.name, fi, call.operand)
    return None


class CallWrite(Enum):
    """Why a call may write (`call_write`)."""
    UNKNOWN = auto()   # no FunctionInfo names the callee
    BODY = auto()      # a body whose writes are not known yet
    GLOBAL = auto()    # a body, while the question involves module globals
    OPERAND = auto()   # a write through the receiver or a writable argument


def call_write(ctx: 'SemanticContext', parts: CallParts,
               written: 'Callable[[TpyExpr], object]', *,
               facts_final: bool, globals_written: bool = False
               ) -> 'tuple[CallWrite, object] | None':
    """Whether the call `parts` may write an operand `written` answers for
    (its truthy answer is the label returned with OPERAND), or anything
    at all. A `@pure` callee writes nothing. Before mutation facts are
    final (`facts_final` False) a callee with a body may write anything;
    once they are, an unanalyzed one declared `@readonly` writes nothing,
    and a body writes its receiver unless it is known not to and an
    argument only at a parameter it may mutate. A builtin binding (or one
    sema handles itself, `special_handling`) writes its receiver unless
    `@readonly` (an `@auto_readonly` accessor's mutable clone does not),
    and an argument at any parameter not declared readonly -- whatever its
    type: a `Span` is a value that writes the list it views."""
    fi = parts.fi
    if fi is None or fi.is_callable_value:
        return CallWrite.UNKNOWN, None
    root = fi.root
    bodyless = root.special_handling or is_bodyless_binding(root)
    if not bodyless and not facts_final:
        return None if root.is_pure else (CallWrite.BODY, None)
    if root.is_pure or (root.is_readonly
                        and root.direct_mutated_params is None):
        return None
    if not bodyless and globals_written:
        return CallWrite.GLOBAL, None
    if (parts.receiver is not None and not root.is_readonly
            and not fi.is_auto_readonly_mutable_clone
            and not (facts_final and root.self_mutated is False)):
        label = written(parts.receiver)
        if label:
            return CallWrite.OPERAND, label
    mutated = root.mutated_params if facts_final else None
    for i, arg in parts.args:
        param = fi.params[i] if i < len(fi.params) else None
        if (param is not None and param.type is not None
                and _readonly_param(param.type)):
            continue
        if mutated is not None and i not in mutated:
            continue
        for sub in _arg_exprs(arg):
            label = written(sub)
            if label:
                return CallWrite.OPERAND, label
    return None


def _user_first(name: str) -> tuple[bool, str]:
    """Sort key naming a user's name before a compiler temporary."""
    return name.startswith("__"), name


def _shown(name: str) -> str:
    """A name for a diagnostic: a compiler temporary has no source spelling."""
    return "a temporary" if name.startswith("__") else f"'{name}'"


def _readonly_param(t) -> bool:
    return isinstance(t, ReadonlyType) or isinstance(
        unwrap_ref_type(t), ReadonlyType)


def _arg_exprs(arg: 'TpyExpr') -> list['TpyExpr']:
    if isinstance(arg, TpyVarargPack):
        return [a.expr if isinstance(a, TpyStarUnpack) else a
                for a in arg.args]
    return [arg]


def _peel(e: 'TpyExpr') -> 'TpyExpr':
    while isinstance(e, TpyCoerce):
        e = e.expr
    return e


def _error(ctx: 'SemanticContext', hold: LoopFrameHold, why: str):
    g = hold.holder
    return ctx.error(
        f"cannot keep '{g}' open across passes of this loop: {why} while "
        f"'{g}' from the previous pass still borrows it; close it with "
        f"'del {g}' before the pass ends, or iterate it inside a helper "
        f"function",
        hold.node)


# -- a loop variable's source written while the variable is live --

# Iterables that build their own storage: nothing else reaches it.
_FRESH_SOURCES = (TpyArrayLiteral, TpyBytesLiteral, TpyDictComprehension,
                  TpyDictLiteral, TpyGeneratorExpression,
                  TpyListComprehension, TpyListRepeat, TpySetComprehension,
                  TpySetLiteral, TpyStrLiteral, TpyTupleLiteral)


@dataclass(frozen=True)
class LoopSource:
    """The storage a loop lends its elements from: the `types` an object
    holding it has to be able to hold, the `roots` of the names it is read
    through (None when one cannot be named), and for a view local the
    `view` whose loans name that storage at the end of the body. An
    `unknown` source copies."""
    types: tuple = ()
    roots: 'frozenset[str] | None' = None
    view: 'str | None' = None
    unknown: bool = False


def loop_source(ctx: 'SemanticContext', iterable: 'TpyExpr',
                elem_type: 'TpyType | None' = None) -> 'LoopSource | None':
    """The storage iterating `iterable` lends its elements from: the
    iterable named by a place, or a call's result and the operands it
    borrows from (`d.items()` lends from `d`). A view lends from the
    storage it borrows (`a[0:2]` from `a`, a view local from what its loans
    name), matched by what can hold its element. None for a source nothing
    else can reach -- a display or a call handing back a fresh value."""
    e = _peel(iterable)
    t = ctx.get_expr_type(e)
    if t is None:
        return LoopSource(unknown=True)
    if isinstance(e, TpySubscript) and isinstance(_peel(e.index), TpySlice):
        return loop_source(ctx, e.obj, elem_type)
    view = is_indirection_type(t)
    if view and elem_type is None:
        return LoopSource(unknown=True)
    if not isinstance(e, (TpyCall, TpyMethodCall)):
        if view:
            local = (isinstance(e, TpyName) and not ctx.is_top_level
                     and e.name not in ctx.func.current_param_names)
            return LoopSource((elem_type,), view=e.name if local else None)
        if isinstance(e, (TpyName, TpySubscript, TpyFieldAccess)):
            root = _root_name_of_expr(e)
            return LoopSource((t,), None if root is None
                              else frozenset((root,)))
        return (None if isinstance(e, _FRESH_SOURCES)
                else LoopSource((t,), None))
    storage = iterated_storage(ctx, e)
    operands = [_peel(op.arg) for op in storage.operands]
    if is_property_getter_read(e):
        operands.append(_peel(e.obj))
    if not (storage.loans or storage.unplaceable or operands):
        if view:
            return LoopSource((elem_type,), None)
        if not call_returns_cpp_ref(None, e.resolved_function_info):
            return None
    roots = [_root_name_of_expr(o) for o in operands] + [
        _storage_root(k) for k, _loan in storage.loans]
    types = [ctx.get_expr_type(o) for o in operands]
    if None in types:
        return LoopSource(unknown=True)
    return LoopSource(
        (t, *types),
        None if (storage.unplaceable or not roots or None in roots)
        else frozenset(r for r in roots if r is not None))


@dataclass(frozen=True)
class LiveRegion:
    """What runs while a loop variable is live: the statements (a for
    statement's body and `else` clause) and expressions (a comprehension
    clause's conditions, element(s) and later clauses)."""
    stmts: tuple = ()
    exprs: tuple = ()


def for_live_region(loop: TpyForEach) -> LiveRegion:
    """A for statement's body and `else` clause; its head runs once before
    the first binding."""
    return LiveRegion(stmts=(*loop.body, *loop.orelse))


def comp_live_region(exprs: 'Iterable[TpyExpr | None]') -> LiveRegion:
    """A comprehension clause's conditions, element(s) and later clauses."""
    return LiveRegion(exprs=tuple(e for e in exprs if e is not None))


@dataclass
class PendingSourceWrite:
    """A loop whose region writes a holding type only through function
    locals (`through`), decided once the body's loans are known
    (`resolve_source_writes`)."""
    func: object
    node: 'TpyForEach | TpyComprehensionGenerator'
    elem_type: 'TpyType | None'
    source: LoopSource
    through: frozenset[str]
    # A variable read after its loop is live for the rest of the body: its
    # proof runs over the whole body once that body is analyzed.
    whole_body: bool = False


def note_source_writes(ctx: 'SemanticContext',
                       node: 'TpyForEach | TpyComprehensionGenerator',
                       iterable: 'TpyExpr', region: LiveRegion,
                       elem_type: 'TpyType | None' = None) -> None:
    """Whether the loop may write its source while its variable is live
    (`_SourceProof`): decided now when the proof fails, at the end of the
    body when a write lands only in function locals whose ownership and
    loans decide it. A variable read after its loop
    (`TpyForEach.var_live_after`) is live for the rest of the function,
    order-free: its proof runs over the whole body at its end."""
    src = loop_source(ctx, iterable, elem_type)
    if src is None:
        return
    if src.unknown:
        stamp_copy_verdicts(ctx, node, elem_type)
        return
    # None: liveness never reached the loop, so the variable may be live.
    if isinstance(node, TpyForEach) and node.var_live_after is not False:
        if ctx.is_top_level or not isinstance(ctx.func.current_function,
                                              TpyFunction):
            stamp_copy_verdicts(ctx, node, elem_type, outliving_only=True)
        else:
            ctx.pending_source_writes.append(PendingSourceWrite(
                ctx.func.current_function, node, elem_type, src,
                frozenset(), whole_body=True))
    proof = _SourceProof(ctx, src.types)
    try:
        proof.run(region)
    except NotInert:
        stamp_copy_verdicts(ctx, node, elem_type)
        return
    if not proof.through:
        return
    if ctx.is_top_level:
        stamp_copy_verdicts(ctx, node, elem_type)
    else:
        ctx.pending_source_writes.append(PendingSourceWrite(
            ctx.func.current_function, node, elem_type, src,
            frozenset(proof.through)))


def resolve_source_writes(ctx: 'SemanticContext', owned: 'set[str]',
                          edges: StorageEdges,
                          frames: 'frozenset[str]') -> None:
    """The pending loops of the body just analyzed. A write rooted at a
    local that owns its storage (every binding a fresh object, `owned`)
    reaches the source only when the source is read through that local or
    through something that borrows from it -- over the body's loan graph
    (`storage_closure`). A view source's storage is what its loans name in
    that graph; a view with none is not traced."""
    fn = ctx.func.current_function
    keep: list[PendingSourceWrite] = []
    # The whole-body proof per source type: the same answer for every loop
    # over a source of those types.
    whole: dict[tuple, 'frozenset[str] | None'] = {}
    for entry in ctx.pending_source_writes:
        if entry.func is not fn:
            keep.append(entry)
            continue
        through = entry.through
        if entry.whole_body:
            key = entry.source.types
            if key not in whole:
                proof = _SourceProof(ctx, key)
                try:
                    proof.run(LiveRegion(stmts=tuple(fn.body)))
                    whole[key] = frozenset(proof.through)
                except NotInert:
                    whole[key] = None
            if whole[key] is None:
                stamp_copy_verdicts(ctx, entry.node, entry.elem_type,
                                    outliving_only=True)
                continue
            through = whole[key]
        roots = entry.source.roots
        if entry.source.view is not None:
            up, _reach = storage_closure(edges, frames, {entry.source.view})
            roots = frozenset(up - {entry.source.view}) or None
        for root in sorted(through):
            if root not in owned or roots is None:
                hit = True
            else:
                _up, reach = storage_closure(edges, frames, {root})
                hit = bool(reach & roots)
            if hit:
                stamp_copy_verdicts(ctx, entry.node, entry.elem_type,
                                    outliving_only=entry.whole_body)
                break
    ctx.pending_source_writes = keep


def resolve_leftover_source_writes(ctx: 'SemanticContext') -> None:
    """A pending loop whose body never reached `resolve_source_writes`
    copies."""
    for entry in ctx.pending_source_writes:
        stamp_copy_verdicts(ctx, entry.node, entry.elem_type,
                            outliving_only=entry.whole_body)
    ctx.pending_source_writes = []


def stamp_copy_verdicts(ctx: 'SemanticContext',
                        node: 'TpyForEach | TpyComprehensionGenerator',
                        elem_type: 'TpyType | None', *,
                        outliving_only: bool = False) -> None:
    """The one copy verdict of each binding the loop makes, read by the
    view rule and the binding planner: the loop variable copies a value
    element (`holds_only_values`; an element naming a type parameter keeps
    its reference: BUGS.md#generic-loop-var-sees-source-write), and a for
    head's value targets copy out of a holder of values (a tuple holding a
    reference type is lifted into a detached `tuple_to_pointer` copy its
    value targets read). `outliving_only`: the rest of the body may write
    the source of a variable read after its loop, so only the bindings that
    outlive the loop copy -- a variable declared before the loop
    (`hoist_loop_var`, the loop's `if_branch_decls`), a head target bound
    to such a name or to an outer one. A verdict once set stays set."""
    if isinstance(node, TpyForEach) and node.is_tuple_unpack:
        head = node.body[0] if node.body else None
        if not isinstance(head, TpyTupleUnpack):
            return
        holder = elem_type is not None and holds_only_values(elem_type)
        copies = list(head.target_copies) or [False] * len(head.targets)
        for i, (name, t) in enumerate(zip(head.targets, head.target_types)):
            if not (holder and isinstance(t, TpyType)
                    and holds_only_values(t)):
                continue
            if outliving_only and not (
                    name in ctx.if_branch_decls.get(node, {})
                    or (i < len(head.is_new) and not head.is_new[i])):
                continue
            copies[i] = True
            ctx.mark_view_source_written(t)
        head.target_copies = copies
        return
    if not isinstance(node, TpyForEach) and node.unpack_vars is not None:
        # An unpack clause copies its value elements out of the holder.
        return
    if outliving_only and not (isinstance(node, TpyForEach) and (
            node.hoist_loop_var
            or node.var in ctx.if_branch_decls.get(node, {}))):
        return
    if elem_type is not None and holds_only_values(elem_type):
        node.copies_var = True
        if isinstance(node, TpyForEach):
            ctx.mark_view_source_written(elem_type)


class _SourceProof(InertWalk):
    """The closed proof that a loop's live region cannot write its source:
    `may_interrupt`'s allowlist walk (every statement and expression of a
    known kind, every value it touches inert), with callees widened to any
    without a body (or `@pure`), stores into any place, comprehensions
    walked and nested `def`s defined, and no `try`, `raise` or suspension;
    and separately no store, `del`, rebind or
    writing call reaching an object whose type can hold the source
    (`type_may_hold`), except through a function local whose type points
    at no other storage (`through`): its identity is the function's to
    see, decided over the body's loans."""

    admits_unwinding = False
    admits_nested = True

    def __init__(self, ctx: 'SemanticContext',
                 sources: 'tuple[TpyType, ...]') -> None:
        fn = ctx.func.current_function
        super().__init__(ctx, fn if isinstance(fn, TpyFunction) else None)
        self.sources = sources
        self.through: set[str] = set()

    def run(self, region: LiveRegion) -> None:
        walk_body_stmts(list(region.stmts), self.walk, self.stmt)
        for e in region.exprs:
            self.walk(e)

    # -- what a write may reach --

    def may_hold(self, t: 'TpyType | None') -> bool:
        if t is None:
            return True
        reg = self.ctx.registry
        if is_indirection_type(t):
            # A write through a view or pointer lands in what it refers to.
            refs = unwrap_qualifiers(t).inner_types()
            return any(type_may_hold(r, s, reg) or type_may_hold(s, r, reg)
                       for r in refs for s in self.sources)
        return any(type_may_hold(t, s, reg) for s in self.sources)

    def written(self, e: 'TpyExpr') -> bool:
        """A write of the object `e`: proven apart from the source, or
        reaching it only through a function local (`through`)."""
        e = _peel(e)
        if not self.may_hold(self.ctx.get_expr_type(e)):
            return False
        node = e
        while isinstance(node, (TpyFieldAccess, TpySubscript)):
            node = _peel(node.obj)
        root = _root_name_of_expr(e)
        t = (self.ctx.get_expr_type(node) if isinstance(node, TpyName)
             and node.name == root else None)
        self.require(root is not None and t is not None
                     and not root.startswith("__")
                     and not may_hold_pointer(t))
        self.through.add(root)  # type: ignore[arg-type]
        return False

    # -- hooks --

    def admitted(self, fi: 'FunctionInfo') -> bool:
        root = fi.root
        return (root.is_pure or root.special_handling
                or is_declaration_only(fi, self.ctx.registry))

    def on_node(self, node: 'TpyExpr | TpyStmt') -> None:
        for call in implicit_calls(node):
            self.on_call(call)

    def on_call(self, call: 'TpyExpr | TpyStmt | ImplicitCall') -> None:
        """One call: into a callee with no body (or `@pure`), writing
        nothing that may hold the source."""
        parts = call_parts(self.ctx, call)
        if parts is None:
            return
        self.require(parts.fi is not None and self.admitted(parts.fi))
        self.require(call_write(self.ctx, parts, self.written,
                                facts_final=False) is None)

    def rebind(self, name: str, t: 'TpyType | None') -> None:
        """A whole-name binding (or `del`) replaces the name's object. A
        name with no known type here was first bound inside the walked
        statements: it holds nothing the source could have been read
        through."""
        if t is None:
            return
        self.require(is_inert_type(t))
        if self.may_hold(t):
            self.require(not may_hold_pointer(t))
            self.through.add(name)

    def target(self, t: 'TpyExpr') -> None:
        """A store into any place: the old object goes (its value is
        checked where the target is walked), and the store writes the
        object it lands in."""
        t = _peel(t)
        if isinstance(t, TpyName):
            super().target(t)
            return
        self.require(isinstance(t, (TpyFieldAccess, TpySubscript))
                     and getattr(t, "property_setter_call", None) is None
                     and not getattr(t, "property_setter", False))
        self.written(t.obj)
