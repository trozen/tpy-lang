"""Fact 1 of the ownership model (`sema/binding_ownership.py`): which leaf
paths of a binding's storage the binding owns, one verdict per binding --
a parameter and the receiver by their signature, a lambda parameter by its
analyzed type, a local by its function's entry, an iteration binder by its
site's -- and which binding a name read resolves to (`resolve_read`)."""

from __future__ import annotations

from ..parse.nodes import (TpyForEach, TpyFunction, TpyLambda,
                           TpyListComprehension, TpyName, TpyVarDecl,
                           TpyNestedDef, TpyTupleUnpack, walk_body_stmts,
                           walk_expr_tree)
from ..thir.testutil import _compile, _entry
from .binding_ownership import (Acquired, CompMove, ReadKind,
                                frame_field_ownership,
                                lambda_param_ownership, local_ownership,
                                param_ownership, read_ownership,
                                receiver_ownership, site_ownership)

_PRELUDE = """\
from typing import Callable, Iterator, Self
from tpy import int32, Own, own_iter
class A:
    def __init__(self, v: int32) -> None:
        self.v = v
    def finish(self: Own[Self]) -> int32:
        return self.v
"""


def _module(source: str):
    _compiler, modules = _compile(_PRELUDE + source)
    entry = _entry(modules)
    return entry.analyzer.ctx, {f.name: f for f in entry.ast.functions}


def _nodes(func: TpyFunction, kind: type) -> list:
    """Every statement and expression of `kind` in `func`'s body, nested
    defs' bodies included, in source order."""
    out: list = []

    def visit(e) -> bool:
        if isinstance(e, kind):
            out.append(e)
        return True

    def stmts(body) -> None:
        def on_stmt(s) -> None:
            if isinstance(s, kind):
                out.append(s)
            if isinstance(s, TpyNestedDef):
                stmts(s.func.body)
        walk_body_stmts(body, lambda e: walk_expr_tree(e, visit), on_stmt)
    stmts(func.body)
    return out


def _paths(own, t=None) -> frozenset:
    return own.owned_paths(t)


def test_parameters_own_what_their_markers_hand_over() -> None:
    ctx, f = _module("""\
def mixed(p: tuple[Own[A], A]) -> int32:
    return p[1].v
def values(p: tuple[Own[int32], int32]) -> int32:
    return p[0]
def nested(p: Own[tuple[tuple[A, int32], int32]]) -> int32:
    return p[1]
def scalar(a: Own[A], b: A, n: int | None, s: str | None) -> int32:
    return a.v + b.v
""")
    tt = f["mixed"].params[0][1]
    own = param_ownership(f["mixed"], "p", tt)
    assert own.acquired is Acquired.HANDED
    assert own.owned_paths(tt) == {(0,)}
    vt = f["values"].params[0][1]
    # A value leaf is owned too: the planner moves this parameter.
    assert param_ownership(f["values"], "p", vt).owned_paths(vt) == {(0,)}
    nt = f["nested"].params[0][1]
    assert param_ownership(f["nested"], "p", nt).owned_paths(nt) == {
        (0, 0), (0, 1), (1,)}
    sc = f["scalar"]
    types = dict(sc.params)
    assert param_ownership(sc, "a", types["a"]).owned_paths(None) == {()}
    assert not param_ownership(sc, "b", types["b"])
    # A by-value expensive Optional holds its own copy; a view owns nothing.
    assert param_ownership(sc, "n", types["n"]).acquired is (
        Acquired.COPIED_PARAM)
    assert not param_ownership(sc, "s", types["s"])


def test_locals_read_the_function_wide_rebinding_fact() -> None:
    ctx, f = _module("""\
def partly(a: A) -> int32:
    u = (a, A(1))
    return u[1].v
def fresh() -> int32:
    u = (A(1), A(2))
    return u[0].v
def borrowed(a: A) -> int32:
    b = a
    return b.v
def rebound_param(n: int) -> int:
    n = n - 1
    q, n = divmod(n, 7)
    return q + n
""")
    u = local_ownership(ctx, f["partly"], "u")
    # The binding borrows element 0: it owns element 1 only.
    assert u.acquired is Acquired.OWNED_LOCAL
    assert _paths(u, _tuple_of(ctx, f["partly"], "u")) == {(1,)}
    fresh = local_ownership(ctx, f["fresh"], "u")
    assert _paths(fresh, _tuple_of(ctx, f["fresh"], "u")) == {(0,), (1,)}
    assert not local_ownership(ctx, f["borrowed"], "b")
    # A parameter an unpack rebinds has a LOCAL entry of its own.
    assert local_ownership(ctx, f["rebound_param"], "n")


def _tuple_of(ctx, func: TpyFunction, name: str):
    reads = [n for n in _nodes(func, TpyName) if n.name == name]
    return ctx.get_expr_type(reads[-1])


def test_iteration_binders_own_by_their_site() -> None:
    ctx, f = _module("""\
def consuming() -> int32:
    xs: list[A] = [A(1), A(2)]
    ys: list[A] = []
    for a in xs:
        ys.append(a)
    return len(ys)
def borrow(w: list[A]) -> list[A]:
    return w
def reverted(w: list[A]) -> int32:
    xs: list[A] = [A(1)]
    ys: list[A] = []
    i = 0
    while i < 2:
        for a in xs:
            ys.append(a)
        xs = borrow(w)
        i += 1
    return len(ys)
def owned_iter(src: Iterator[Own[A]]) -> int32:
    ys: list[A] = []
    for a in src:
        ys.append(a)
    return len(ys)
def from_own_iter() -> int32:
    xs: list[A] = [A(1)]
    ys: list[A] = []
    for a in own_iter(xs):
        ys.append(a)
    return len(ys)
def copied_head(a: list[tuple[int, str]]) -> None:
    for x, s in a:
        a[0] = (50, "changed")
        print(x, s)
def clause(src: Iterator[Own[A]]) -> Own[list[A]]:
    return [a for a in src]
""")
    loop = _nodes(f["consuming"], TpyForEach)[0]
    assert site_ownership(ctx, loop, "a").acquired is Acquired.CONSUMING_LOOP
    # Settlement reverts a consuming loop whose iterable the back edge
    # rebinds to borrowed storage: its variable owns nothing.
    loop = _nodes(f["reverted"], TpyForEach)[0]
    assert not site_ownership(ctx, loop, "a")
    loop = _nodes(f["owned_iter"], TpyForEach)[0]
    assert site_ownership(ctx, loop, "a")
    loop = _nodes(f["from_own_iter"], TpyForEach)[0]
    assert site_ownership(ctx, loop, "a")
    head = _nodes(f["copied_head"], TpyTupleUnpack)[0]
    assert head.is_loop_head
    # The source is written in the body: each target takes its own copy.
    for n in ("x", "s"):
        assert site_ownership(ctx, head, n).acquired is Acquired.COPIED_STEP
    comp = _nodes(f["clause"], TpyListComprehension)[0]
    gen = comp.generators[0]
    assert site_ownership(ctx, gen, "a").acquired is Acquired.OWNING_CLAUSE
    # The clause's move is its recorded decision, read once.
    read = comp.element_expr
    while not isinstance(read, TpyName):
        read = read.expr
    assert ctx.read_bindings[read].node is gen
    assert ctx.comp_moves.get(read) is CompMove.LAST_SINK


def test_non_owning_bindings_own_nothing() -> None:
    ctx, f = _module("""\
G = A(1)
def ident[T](x: T) -> T:
    return x
def lending[T](xs: list[T], x: T) -> None:
    y = ident(x)
    xs.append(y)
def handler() -> int32:
    try:
        raise ValueError("x")
    except ValueError as e:
        print(e)
    return 0
def glob() -> int32:
    return G.v
""")
    # A local bound off a `val_or_ref_t` result borrows.
    assert not local_ownership(ctx, f["lending"], "y")
    assert not local_ownership(ctx, f["handler"], "e")
    read = [n for n in _nodes(f["glob"], TpyName) if n.name == "G"][0]
    assert ctx.read_bindings[read].kind is ReadKind.MODULE
    assert not read_ownership(ctx, ctx.read_bindings[read])


def test_nested_defs_and_lambdas_own_by_their_own_node() -> None:
    ctx, f = _module("""\
def outer(p: A) -> int32:
    def inner(q: Own[A]) -> int32:
        b = A(2)
        return q.v + b.v + p.v
    g: Callable[[tuple[Own[A], int32]], Own[tuple[A, int32]]] = lambda p: p
    return inner(A(1)) + g((A(3), 1))[1]
""")
    inner = _nodes(f["outer"], TpyNestedDef)[0].func
    assert param_ownership(inner, "q", dict(inner.params)["q"])
    assert local_ownership(ctx, inner, "b")
    # The enclosing parameter read inside the nested def is a capture.
    read = [n for n in _nodes(inner, TpyName) if n.name == "p"][0]
    assert ctx.read_bindings[read].kind is ReadKind.CAPTURED
    # The lambda's `p` shadows the outer `p: A`: its read resolves to the
    # lambda, which owns what its ownership-transfer type marks.
    lam = _nodes(f["outer"], TpyLambda)[0]
    body = lam.body
    while not isinstance(body, TpyName):
        body = body.expr
    binding = ctx.read_bindings[body]
    assert binding.kind is ReadKind.LAMBDA and binding.node is lam
    own = read_ownership(ctx, binding)
    assert own.owned_paths(lam.inferred_param_types[0]) == {(0,)}
    assert not lambda_param_ownership(None)


def test_a_loop_site_keeps_its_ownership_past_a_later_borrow() -> None:
    ctx, f = _module("""\
class H:
    a: A
    def __init__(self) -> None:
        self.a = A(0)
def mixed_name(h: H, borrowed: A) -> None:
    xs: list[A] = [A(1)]
    ys: list[A] = []
    for x in xs:
        if x.v > 0:
            ys.append(x)
        else:
            h.a = x
    x = borrowed
    print(x.v)
""")
    func = f["mixed_name"]
    loop = _nodes(func, TpyForEach)[0]
    # The later `x = borrowed` revokes the LOCAL entry, not the loop's.
    assert site_ownership(ctx, loop, "x")
    assert not local_ownership(ctx, func, "x")
    reads = [n for n in _nodes(func, TpyName) if n.name == "x"]
    assert ctx.read_bindings[reads[1]].node is loop
    assert ctx.read_bindings[reads[-1]].kind is ReadKind.LOCAL


def test_a_frame_field_only_loops_bind_owns_by_every_site() -> None:
    ctx, f = _module("""\
def gen(ys: list[A]) -> Iterator[int32]:
    xs: list[A] = [A(1)]
    for a in own_iter(xs):
        yield a.v
    for a in ys:
        yield a.v
def gen2() -> Iterator[int32]:
    xs: list[A] = [A(1)]
    ys: list[A] = [A(2)]
    for a in own_iter(xs):
        yield a.v
    for a in own_iter(ys):
        yield a.v
def element_first(xs: Iterator[Own[A]]) -> Iterator[int32]:
    ys: list[A] = [A(1)]
    for a in xs:
        yield a.v
    for a in own_iter(ys):
        yield a.v
def own_iter_first(xs: Iterator[Own[A]]) -> Iterator[int32]:
    ys: list[A] = [A(1)]
    for a in own_iter(ys):
        yield a.v
    for a in xs:
        yield a.v
""")
    # One of its loops borrows: the field owns nothing, whichever came
    # first.
    assert not frame_field_ownership(ctx, f["gen"], "a")
    assert frame_field_ownership(ctx, f["gen2"], "a")
    # Loops that acquire differently give one entry in either order.
    first = frame_field_ownership(ctx, f["element_first"], "a")
    second = frame_field_ownership(ctx, f["own_iter_first"], "a")
    assert first == second
    assert first.acquired is Acquired.OWN_ITER_LOOP


def test_the_consuming_receiver_is_handed_over() -> None:
    _compiler, modules = _compile(_PRELUDE + """\
def use(a: A) -> int32:
    return a.v
""")
    entry = _entry(modules)
    ctx = entry.analyzer.ctx
    finish = next(m for r in entry.ast.all_records() if r.name == "A"
                  for m in r.methods if m.name == "finish")
    own = receiver_ownership(finish)
    assert own.acquired is Acquired.HANDED
    assert own.owned_paths(None) == {()}
    # A `self` read in the consuming method resolves to its receiver.
    read = [n for n in _nodes(finish, TpyName) if n.name == "self"][0]
    binding = ctx.read_bindings[read]
    assert binding.kind is ReadKind.RECEIVER and binding.node is finish
    assert read_ownership(ctx, binding).owned_paths(None) == {()}
    use = next(f for f in entry.ast.functions if f.name == "use")
    assert not receiver_ownership(use)


def test_an_iteration_site_reads_only_its_own_body_rebinds() -> None:
    ctx, f = _module("""\
def pairs() -> Iterator[tuple[int32, Own[A]]]:
    yield (1, A(5))
def loop_rebind(xs: Iterator[Own[A]], b: A, out: list[A]) -> None:
    for a in xs:
        a = b
        out.append(a)
def loop_fresh(xs: Iterator[Own[A]], b: A, out: list[A]) -> None:
    for a in xs:
        a = A(4)
        out.append(a)
    a = b
    print(a.v)
def head_rebound(b: A, out: list[A]) -> None:
    for n, a in pairs():
        a = b
        out.append(a)
def head_kept(out: list[A]) -> None:
    for n, a in pairs():
        out.append(a)
""")
    def loop(fn: str):
        return _nodes(f[fn], TpyForEach)[0]

    def head(fn: str):
        return _nodes(f[fn], TpyTupleUnpack)[0]
    # A borrowing rebind inside the loop's body revokes the site's
    # ownership; one after the loop is the LOCAL entry's, not the site's.
    assert not site_ownership(ctx, loop("loop_rebind"), "a")
    assert site_ownership(ctx, loop("loop_fresh"), "a").acquired is (
        Acquired.OWN_ELEMENT)
    assert not local_ownership(ctx, f["loop_fresh"], "a")
    # A loop-head target, the same.
    assert not site_ownership(ctx, head("head_rebound"), "a")
    assert site_ownership(ctx, head("head_kept"), "a").acquired is (
        Acquired.OWN_ELEMENT)


def test_with_except_and_match_targets_own_by_their_site() -> None:
    ctx, f = _module("""\
class E(Exception):
    pass
class Val:
    def __enter__(self) -> int32:
        return 3
    def __exit__(self, et, ev, tb) -> None:
        pass
class Ref:
    a: A
    def __init__(self) -> None:
        self.a = A(1)
    def __enter__(self) -> A:
        return self.a
    def __exit__(self, et, ev, tb) -> None:
        pass
def handler(out: list[E]) -> None:
    a = E()
    print(a)
    try:
        raise E()
    except E as a:
        print(a)
def withs(r: Ref) -> int32:
    with Val() as n:
        with r as a:
            return n + a.v
def matches(o: A | None, k: int32) -> int32:
    match k:
        case 0:
            return 0
        case m:
            pass
    match o:
        case A() as x:
            return x.v + m
        case _:
            return m
""")
    # An except target refers to the exception in flight, whatever local
    # shares its spelling.
    h = f["handler"]
    handler = h.body[2].handlers[0]
    reads = [n for n in _nodes(h, TpyName) if n.name == "a"]
    assert ctx.read_bindings[reads[-1]].node is handler
    assert not read_ownership(ctx, ctx.read_bindings[reads[-1]])
    assert local_ownership(ctx, h, "a")
    # A value enter result's target owns its copy; a record's refers to
    # the manager's storage.
    outer = f["withs"].body[0]
    inner = outer.body[0]
    assert site_ownership(ctx, outer.items[0], "n").acquired is (
        Acquired.COPIED_VALUE)
    assert not site_ownership(ctx, inner.items[0], "a")
    reads = [n for n in _nodes(f["withs"], TpyName) if n.name == "a"]
    assert ctx.read_bindings[reads[-1]].node is inner.items[0]
    # A by-value capture owns its copy; a record capture aliases.
    first, second = f["matches"].body[0], f["matches"].body[1]
    assert site_ownership(ctx, first.cases[1], "m").acquired is (
        Acquired.COPIED_VALUE)
    assert not site_ownership(ctx, second.cases[0], "x")
    reads = [n for n in _nodes(f["matches"], TpyName) if n.name == "x"]
    assert ctx.read_bindings[reads[-1]].node is second.cases[0]


def test_generator_walrus_frame_and_module_bindings() -> None:
    _compiler, modules = _compile(_PRELUDE + """\
def nums() -> Iterator[int32]:
    yield 1
def gen_local() -> int32:
    t = nums()
    for n in t:
        print(n)
    return 0
def walrus(k: int32, out: list[A]) -> None:
    if (w := A(k)).v > 0:
        out.append(w)
async def frame(k: int32) -> int32:
    a = A(k)
    return a.v
TOP = A(3)
for x in [A(1)]:
    print(x.v, TOP.v)
""")
    entry = _entry(modules)
    ctx = entry.analyzer.ctx
    f = {fn.name: fn for fn in entry.ast.functions}
    # A generator object owns its frame (that it never moves is the
    # planner's).
    assert local_ownership(ctx, f["gen_local"], "t").acquired is (
        Acquired.OWNED_LOCAL)
    assert local_ownership(ctx, f["walrus"], "w").acquired is (
        Acquired.OWNED_LOCAL)
    # An async body's local is its frame field.
    assert frame_field_ownership(ctx, f["frame"], "a")
    # A module's names are module storage; its loop has a site entry of its
    # own (a borrow of each element here).
    reads = [e for st in entry.ast.top_level_stmts
             for e in _stmt_names(st) if e.name == "TOP"]
    assert reads and all(
        ctx.read_bindings[r].kind is ReadKind.MODULE for r in reads)
    loop = next(st for st in entry.ast.top_level_stmts
                if isinstance(st, TpyForEach))
    assert loop in ctx.site_ownership
    assert not site_ownership(ctx, loop, "x")


def _stmt_names(st) -> list:
    out: list = []
    walk_body_stmts([st], lambda e: walk_expr_tree(
        e, lambda x: out.append(x) or True), lambda s: None)
    return [e for e in out if isinstance(e, TpyName)]


def test_a_val_or_ref_declaration_owns_nothing_a_slot_owns() -> None:
    _compiler, modules = _compile(_PRELUDE + """\
from tpy import copy, Fn
def ident[T](x: T) -> T:
    return x
def mk[T](x: T) -> Own[T]:
    return copy(x)
def single[T](xs: list[T], x: T) -> None:
    y = ident(x)
    xs.append(y)
def reassigned[T](xs: list[T], a: T, x: T) -> None:
    y = mk(a)
    xs.append(y)
    y = ident(x)
    xs.append(y)
def returned[T](a: T, x: T) -> Own[T]:
    y = ident(x)
    y = mk(a)
    return y
def hoisted[T](xs: list[T], x: T) -> None:
    try:
        y = ident(x)
    finally:
        print("f")
    xs.append(y)
def frame[T](xs: list[T], x: T) -> Iterator[int32]:
    y = ident(x)
    xs.append(y)
    yield 1
def callable_value[T](f: Fn[[T], T], xs: list[T], x: T) -> None:
    y = f(x)
    xs.append(y)
""")
    entry = _entry(modules)
    ctx = entry.analyzer.ctx
    f = {fn.name: fn for fn in entry.ast.functions}

    def decls(fn: str) -> list:
        return [d for d in _nodes(f[fn], TpyVarDecl) if d.name == "y"]
    # Bound once to the call's `val_or_ref_t` result, the local borrows: a
    # callable value's result included (the C++ binds it the same way).
    for fn in ("single", "callable_value"):
        assert decls(fn)[0] in ctx.val_or_ref_decls, fn
        assert not local_ownership(ctx, f[fn], "y"), fn
    # A slot of its own takes the result as its own: a reassigned or
    # hoisted local, and a resumable frame's field. (A moved-through local
    # rebound off the call is a shape the lowering refuses.)
    for fn in ("reassigned", "returned", "hoisted"):
        assert not any(d in ctx.val_or_ref_decls for d in decls(fn)), fn
        assert local_ownership(ctx, f[fn], "y"), fn
    assert not any(d in ctx.val_or_ref_decls for d in decls("frame"))
    assert frame_field_ownership(ctx, f["frame"], "y")


def test_binders_open_and_close_with_their_block() -> None:
    ctx, f = _module("""\
from tpy import error_return, ReturnException
class RE(Exception, ReturnException):
    pass
class E(Exception):
    def __init__(self, x: int32) -> None:
        super().__init__("e")
        self.x = x
@error_return(RE)
def risky(k: int32) -> int32:
    if k < 0:
        raise RE()
    return k
def return_tier(k: int32) -> int32:
    try:
        n = risky(k)
    except RE as e:
        print(e)
        return 0
    return n
def inner_handler(xs: list[E]) -> None:
    for a in xs:
        try:
            raise E(1)
        except E as a:
            print(a.x)
        print(a.x)
""")
    # The return tier's handler keys its target as the throw tier's does.
    rt = f["return_tier"]
    handler = rt.body[0].handlers[0]
    read = [n for n in _nodes(rt, TpyName) if n.name == "e"][0]
    assert ctx.read_bindings[read].node is handler
    assert not site_ownership(ctx, handler, "e")
    # After an inner handler rebinding the loop variable's name, a read
    # resolves to the loop again.
    ih = f["inner_handler"]
    loop = ih.body[0]
    handler = loop.body[0].handlers[0]
    reads = [n for n in _nodes(ih, TpyName) if n.name == "a"]
    assert [ctx.read_bindings[r].node for r in reads] == [handler, loop]


def test_a_module_level_loop_site_reads_its_body_rebinds() -> None:
    _compiler, modules = _compile(_PRELUDE + """\
def mk() -> Iterator[Own[A]]:
    yield A(1)
b = A(9)
out: list[A] = []
for a in mk():
    a = b
    out.append(a)
for c in mk():
    out.append(c)
""")
    entry = _entry(modules)
    ctx = entry.analyzer.ctx
    rebound, kept = [st for st in entry.ast.top_level_stmts
                     if isinstance(st, TpyForEach)]
    # The module's names are no LOCAL, but its loop sites still subtract
    # the borrowing rebinds inside their bodies.
    assert not site_ownership(ctx, rebound, "a")
    assert site_ownership(ctx, kept, "c").acquired is Acquired.OWN_ELEMENT


def test_frames_globals_and_loop_else_blocks() -> None:
    ctx, f = _module("""\
def ident[T](x: T) -> T:
    return x
async def async_slot[T](x: T, out: list[T]) -> int32:
    y = ident(x)
    out.append(y)
    return 1
def mk() -> Iterator[Own[A]]:
    yield A(1)
def loop_else(b: A, out: list[A]) -> None:
    for a in mk():
        out.append(a)
    else:
        a = b
        out.append(a)
G = "old"
def write_global(c: bool) -> str:
    global G
    if c:
        G = "new"
    return G
def capture_global(k: str, xs: list[str], out: list[str]) -> None:
    global G
    match k:
        case G:
            G = "new"
            out.append(G)
    for G in xs:
        out.append(G)
""")
    # A coroutine's field holds its own slot, as a generator's does.
    assert frame_field_ownership(ctx, f["async_slot"], "y")
    # A loop's `else` block is no pass of it: its rebind is not the site's.
    loop = _nodes(f["loop_else"], TpyForEach)[0]
    assert site_ownership(ctx, loop, "a").acquired is Acquired.OWN_ELEMENT
    # A declared global is module storage: no LOCAL, and its reads resolve
    # to the module.
    wg = f["write_global"]
    assert not local_ownership(ctx, wg, "G")
    reads = [n for n in _nodes(wg, TpyName) if n.name == "G"]
    assert reads and all(
        ctx.read_bindings[r].kind is ReadKind.MODULE for r in reads)
    # A statement's target of a declared global binds module storage: a
    # match capture and a for target own nothing, their reads the module.
    cg = f["capture_global"]
    case = cg.body[1].cases[0]
    loop = _nodes(cg, TpyForEach)[0]
    assert not site_ownership(ctx, case, "G")
    assert not site_ownership(ctx, loop, "G")
    reads = [n for n in _nodes(cg, TpyName) if n.name == "G"]
    assert reads and all(
        ctx.read_bindings[r].kind is ReadKind.MODULE for r in reads)


def test_statement_targets_of_a_declared_global_are_module_storage() -> None:
    ctx, f = _module("""\
class E(Exception):
    def __init__(self, v: int32) -> None:
        super().__init__("e")
        self.v = v
class Val:
    def __enter__(self) -> str:
        return "v"
    def __exit__(self, et, ev, tb) -> None:
        pass
G = "old"
X = E(0)
def with_target() -> str:
    global G
    with Val() as G:
        print(G)
    return G
def head_target(pairs: list[tuple[int32, str]]) -> str:
    global G
    for k, G in pairs:
        print(k, G)
    return G
def clause(xs: list[str]) -> int32:
    global G
    ys = [G for G in xs]
    return len(ys)
def handler(k: int32) -> None:
    global X
    try:
        raise E(k)
    except E as X:
        print(X.v)
""")
    def reads(fn: str, name: str) -> list:
        return [n for n in _nodes(f[fn], TpyName) if n.name == name]
    # A `with` target and a loop-head target of a declared global own
    # nothing, and every read of the name resolves to the module.
    item = f["with_target"].body[1].items[0]
    head = _nodes(f["head_target"], TpyTupleUnpack)[0]
    assert not site_ownership(ctx, item, "G")
    assert not site_ownership(ctx, head, "G")
    for fn in ("with_target", "head_target"):
        assert all(ctx.read_bindings[r].kind is ReadKind.MODULE
                   for r in reads(fn, "G")), fn
    # A comprehension's variable is its own, global or not.
    comp = _nodes(f["clause"], TpyListComprehension)[0]
    read = comp.element_expr
    assert ctx.read_bindings[read].kind is ReadKind.SITE
    assert ctx.read_bindings[read].node is comp.generators[0]
    # An `except` target of a declared global reads the module, while the
    # C++ catches into a local (BUGS.md#except-target-declared-global-local).
    assert all(ctx.read_bindings[r].kind is ReadKind.MODULE
               for r in reads("handler", "X"))
    # The loop head's other target is the head's own.
    head_reads = [n for n in _nodes(f["head_target"], TpyName)
                  if n.name == "k"]
    assert head_reads and all(ctx.read_bindings[r].node is head
                              for r in head_reads)


def test_a_declared_global_owns_nothing_in_every_output() -> None:
    _compiler, modules = _compile(_PRELUDE + """\
from tpy import error_return, ReturnException
class RE(Exception, ReturnException):
    pass
@error_return(RE)
def risky(k: int32) -> int32:
    if k < 0:
        raise RE()
    return k
def mk() -> Iterator[Own[A]]:
    yield A(1)
G = A(0)
X = RE()
def gen(out: list[A]) -> Iterator[int32]:
    global G
    for G in mk():
        yield G.v
        out.append(G)
S = "s"
def gen_head(xs: list[tuple[int32, str]]) -> Iterator[int32]:
    global S
    for k, S in xs:
        xs[0] = (5, "x")
        yield k
def clause() -> int32:
    global G
    ys = [G for G in mk()]
    return len(ys)
@error_return(RE)
def return_tier(k: int32) -> int32:
    global X
    try:
        n = risky(k)
    except RE as X:
        print(X)
        return 0
    return n
""")
    entry = _entry(modules)
    ctx = entry.analyzer.ctx
    f = {fn.name: fn for fn in entry.ast.functions}
    # A frame field only a loop over a declared global binds: module
    # storage, at the site and as the frame field alike.
    loop = _nodes(f["gen"], TpyForEach)[0]
    assert not site_ownership(ctx, loop, "G")
    assert not frame_field_ownership(ctx, f["gen"], "G")
    # So is a loop-head target of one, though the written source makes it
    # copy each step (a `str` global: sema refuses an unpack that rebinds
    # a non-value global, `Cannot reassign global variable`).
    head = _nodes(f["gen_head"], TpyTupleUnpack)[0]
    assert not site_ownership(ctx, head, "S")
    assert not frame_field_ownership(ctx, f["gen_head"], "S")
    # A comprehension's variable is its own: its owning clause still owns.
    gen = _nodes(f["clause"], TpyListComprehension)[0].generators[0]
    assert site_ownership(ctx, gen, "G").acquired is Acquired.OWNING_CLAUSE
    # The return tier's handler of a declared global reads the module,
    # while the C++ binds a local (BUGS.md#except-target-declared-global-local).
    reads = [n for n in _nodes(f["return_tier"], TpyName) if n.name == "X"]
    assert reads and all(
        ctx.read_bindings[r].kind is ReadKind.MODULE for r in reads)
