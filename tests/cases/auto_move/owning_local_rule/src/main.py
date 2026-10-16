# A local one binding (any branch or loop) points at existing storage copies, warned, at
# every owning sink; an owning local still moves (@nocopy `Cell` catches a stray copy).
import asyncio
from typing import Iterable, Iterator, Self, overload
from tpy import Own, ReturnException, copy, error_return, int32, nocopy, readonly


class Box:
    xs: list[int32]

    def __init__(self, n: int32) -> None:
        self.xs = [n]

    def finish(self: Own[Self]) -> Own[Self]:
        return self

    def items(self) -> list[int32]:
        return self.xs


@nocopy
class Cell:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def finish(self: Own[Self]) -> Own[Self]:
        return self


class Holder:
    b: Box

    def __init__(self, b: Own[Box]) -> None:
        self.b = b


class Pair:
    pair: tuple[Box, Box]

    def __init__(self) -> None:
        self.pair = (Box(7), Box(8))


class Ctx:
    inner: Box

    def __init__(self) -> None:
        self.inner = Box(5)

    def __enter__(self) -> Box:
        return self.inner

    def __exit__(self, a, b, c) -> None:
        pass


class Fail(Exception, ReturnException):
    pass


def take(b: Own[Box]) -> int32:
    return len(b.xs)


def make_pair(n: int32) -> tuple[Own[Box], Own[Box]]:
    return (Box(n), Box(n + 1))


def make_mixed(n: int32, b: Box) -> tuple[Own[Box], Box]:
    return (Box(n), b)


def borrow(w: list[Box]) -> list[Box]:
    return w


def head(h: Pair) -> int32:
    a, b = h.pair
    return len(a.xs)


def take_cell(c: Own[Cell]) -> int32:
    return c.n


# free function: an `Own` return of a local one branch binds to the caller's object
def ret(w: Box, c: bool) -> Own[Box]:
    b = w
    if c:
        b = Box(1)
    return b  # tpyc: warning(/copies Box into owned storage; use copy\(\) to make this explicit \('b' is bound to existing storage at line \d+\)/)


# free function: an `Own` argument
def own_arg(w: Box, c: bool) -> int32:
    b = w
    if c:
        b = Box(1)
    return take(b)  # tpyc: warning(/copies Box into owned storage.*'b' is bound to existing storage/)


# free function: a container insert
def append(w: Box, c: bool) -> Own[list[Box]]:
    b = w
    if c:
        b = Box(1)
    out: list[Box] = []
    out.append(b)  # tpyc: warning(/copies Box into owned storage.*'b' is bound to existing storage/)
    return out


# free function: a list literal element
def literal(w: Box, c: bool) -> Own[list[Box]]:
    b = w
    if c:
        b = Box(1)
    return [b]  # tpyc: warning(/copies Box into .*'b' is bound to existing storage/)


# free function: a consuming method call on a rebound local
def receiver(w: Box, c: bool) -> Own[Box]:
    b = w
    if c:
        b = Box(1)
    return b.finish()  # tpyc: warning(/copies Box into owned storage.*'b' is bound to existing storage/)


# free function: a consuming call on an owning local a call result still borrows
def recv_lent() -> Own[Box]:
    b = Box(1)
    v = b.items()
    r = b.finish()  # tpyc: warning(/copies Box into owned storage/)
    print("recv_lent", len(v), r.xs)
    return r


# free function: an owning local's consuming call with no live borrower moves
def recv_owning() -> Own[Cell]:
    b = Cell(2)
    return b.finish()  # tpyc: ok


# free function: a repeated literal's element is a list literal element
def repeat(w: Box, c: bool) -> Own[list[Box]]:
    b = w
    if c:
        b = Box(1)
    return [b] * 2  # tpyc: warning(/copies Box into owned storage.*'b' is bound to existing storage/)


# free function: a consuming method call on a borrowed parameter
def param_receiver(w: Box) -> Own[Box]:
    return w.finish()  # tpyc: warning(/copies Box into owned storage; use copy\(\) to make this explicit$/)


# free function: an owning local keeps its move, every binding a new object
def keep(c: bool) -> Own[Cell]:
    b = Cell(1)
    if c:
        b = Cell(2)
    return b  # tpyc: ok


# free function: `b = a` at `a`'s last use hands the object over
def handover() -> Own[Cell]:
    a = Cell(7)
    b = a  # tpyc: ok
    return b  # tpyc: ok


# free function: an `Own` parameter rebound keeps owning its own storage
def own_param(o: Own[Box], w: Box) -> Own[Box]:
    o = w  # tpyc: warning(/copies Box into owned storage/)
    return o  # tpyc: ok


# free function: a tuple element one binding borrows, at an `Own` return
def elem_return(h: Pair, c: bool) -> Own[Box]:
    p = make_pair(1)
    if c:
        p = h.pair
    return p[0]  # tpyc: warning(/copies Box into owned storage.*'p' is bound to existing storage at line \d+\)/)


# free function: a borrowed tuple element at an `Own` argument
def elem_arg(v: Box, w: Box, c: bool) -> int32:
    p = (w, v)
    if c:
        p = make_pair(1)
    return take(p[0])  # tpyc: warning(/copies Box into owned storage \(tuple element 0\).*'p' is bound to existing storage/)


# free function: a whole tuple no element of which moves
def whole(h: Pair, c: bool) -> Own[tuple[Box, Box]]:
    p = make_pair(1)
    if c:
        p = h.pair
    return p  # tpyc: warning(/tuple element 0\).*'p' is bound/) warning(/tuple element 1\).*'p' is bound/)


# free function: a whole tuple a literal binding borrows and a mixed call half owns
def whole_literal(v: Box, w: Box, c: bool) -> Own[tuple[Box, Box]]:
    p = (w, v)
    if c:
        p = make_mixed(1, v)
    return p  # tpyc: warning(/tuple element 0\).*'p' is bound/) warning(/tuple element 1\).*'p' is bound/)


# free function: a whole tuple whose borrowed Optional element copies null-checked
def opt_whole(w: Box | None, c: bool) -> Own[tuple[Box | None, Box]]:
    # A read-only Optional parameter in a pointer tuple does not build yet
    # (BUGS.md#readonly-optional-param-pointer-tuple-build).
    if w is not None:
        w.xs.append(0)
    p = (w, Box(1))
    if c:
        p = (w, Box(3))
    return p  # tpyc: warning(/copies Box \| None into owned storage \(tuple element 0\).*'p' is bound/)


# loop back edge: the borrowing rebind comes after the return it reaches
def back_edge(ws: list[Box]) -> Own[Box]:
    b = Box(1)
    i = 0
    while i < len(ws):
        if i == 1:
            return b  # tpyc: warning(/copies Box into owned storage.*'b' is bound to existing storage at line \d+\)/)
        b = ws[i]
        i += 1
    return Box(0)


# loop back edge: a tuple element read before the borrowing rebind
def elem_back_edge(h: Pair, n: int32) -> Own[Box]:
    p = make_pair(1)
    i = 0
    while i < n:
        if i == 1:
            return p[0]  # tpyc: warning(/copies Box into owned storage.*'p' is bound to existing storage at line \d+\)/)
        p = h.pair
        i += 1
    return Box(0)


# loop back edge: a consuming `for` over a list a later binding borrows
def loop_back_edge(w: list[Box]) -> Own[list[Box]]:
    xs: list[Box] = [Box(1)]
    out: list[Box] = []
    i = 0
    while i < 2:
        for x in xs:
            out.append(x)  # tpyc: warning(/copies Box into owned storage/)
        xs = borrow(w)
        i += 1
    return out


# try/finally + back edge: the deferred return of a local a later pass borrows copies
def fin_back(ws: list[Box]) -> Own[Box]:
    b = Box(1)
    i = 0
    while i < len(ws):
        if i == 1:
            try:
                return b  # tpyc: warning(/copies Box into owned storage.*'b' is bound to existing storage at line \d+\)/)
            finally:
                print("fin_back finally")
        b = ws[i]
        i += 1
    return Box(0)


# try/finally + back edge: the same for a deferred tuple member
def fin_tup_back(ws: list[Box]) -> tuple[Own[Box], int32]:
    b = Box(1)
    i = 0
    while i < len(ws):
        if i == 1:
            try:
                return (b, i)  # tpyc: warning(/copies Box into owned storage \(tuple element 0\).*'b' is bound/)
            finally:
                print("fin_tup_back finally")
        b = ws[i]
        i += 1
    return (Box(0), 0)


# closure: an owning local's consuming call inside a nested def still moves
def nested_receiver() -> Own[Cell]:
    def inner() -> Own[Cell]:
        b = Cell(1)
        b.n += 1
        return b.finish()  # tpyc: ok
    return inner()


# unpack: a target of a borrowed tuple is non-owning, even if a branch rebinds it
def unpacked(p: tuple[Box, int32], c: bool) -> Own[Box]:
    b, k = p
    if c:
        b = Box(2)
    return b  # tpyc: warning(/copies Box into owned storage.*'b' is bound to existing storage/)


# for loop: a tuple head target borrows the element
def for_head(ps: list[tuple[Box, int32]]) -> Own[Box]:
    for b, k in ps:
        if k == 1:
            return b  # tpyc: warning(/copies Box into owned storage/)
    return Box(0)


# ternary: an arm that names existing storage makes the local non-owning
def ternary(w: Box, c: bool) -> Own[Box]:
    b = w if c else Box(1)
    return b  # tpyc: warning(/copies Box into owned storage.*'b' is bound to existing storage/)


# owning parameters: `Own[T] | None`, a tuple's `Own` element, `readonly[Own[T]]` still move
def opt_own(o: Own[Cell] | None) -> int32:
    if o is not None:
        return take_cell(o)  # tpyc: ok
    return 0


def tup_own(p: tuple[Own[Cell], int32]) -> int32:
    return take_cell(p[0])  # tpyc: ok


def ro_own(o: readonly[Own[Cell]]) -> int32:
    return take_cell(o)  # tpyc: ok


# explicit copy: `copy(b)` at the sink says the copy, so nothing warns
def explicit(w: Box, c: bool) -> Own[Box]:
    b = w
    if c:
        b = Box(1)
    return copy(b)  # tpyc: ok


# a second name: the fresh value bound to a name of its own moves
def second_name(w: Cell) -> Own[Cell]:
    b = w
    print("second_name", b.n)
    d = Cell(2)
    return d  # tpyc: ok


class M:
    # method: an `Own` return
    def ret(self, w: Box, c: bool) -> Own[Box]:
        b = w
        if c:
            b = Box(1)
        return b  # tpyc: warning(/copies Box into owned storage.*'b' is bound to existing storage/)

    # method: an `Own` argument
    def own_arg(self, w: Box, c: bool) -> int32:
        b = w
        if c:
            b = Box(1)
        return take(b)  # tpyc: warning(/copies Box into owned storage.*'b' is bound to existing storage/)

    # method: a consuming method call
    def receiver(self, w: Box, c: bool) -> Own[Box]:
        b = w
        if c:
            b = Box(1)
        return b.finish()  # tpyc: warning(/copies Box into owned storage.*'b' is bound to existing storage/)


# constructor: an `Own` argument to a constructor
def ctor(w: Box, c: bool) -> Own[Holder]:
    b = w
    if c:
        b = Box(1)
    return Holder(b)  # tpyc: warning(/copies Box into owned storage.*'b' is bound to existing storage/)


# closure: the nested def settles its own locals
def nested(w: Box, c: bool) -> Own[Box]:
    def inner() -> Own[Box]:
        b = w
        if c:
            b = Box(1)
        return b  # tpyc: warning(/copies Box into owned storage.*'b' is bound to existing storage/)
    return inner()


# generator: an owning local moves into an `Own` argument, a borrowed parameter copies
def gen(w: Box) -> Iterator[int32]:
    b = Box(3)
    yield take(b)  # tpyc: ok
    yield take(w)  # tpyc: warning(/copies Box into owned storage/)


# match arm: a capture of an lvalue subject is non-owning
def matcher(w: Box | None) -> Own[Box]:
    match w:
        case Box() as b:
            return b  # tpyc: warning(/copies Box into owned storage.*'b' is bound to existing storage/)
        case _:
            return Box(0)


# try/finally: the return a finally defers
def tried(w: Box, c: bool) -> Own[Box]:
    b = w
    if c:
        b = Box(1)
    try:
        return b  # tpyc: warning(/copies Box into owned storage.*'b' is bound to existing storage/)
    finally:
        print("try finally")


# @error_return: an `Own` return
@error_return(Fail)
def er(w: Box, c: bool) -> Own[Box]:
    b = w
    if c:
        b = Box(1)
    return b  # tpyc: warning(/copies Box into owned storage.*'b' is bound to existing storage/)


# with body: the `as` target borrows what `__enter__` returns
def with_as(cx: Ctx) -> Own[Box]:
    with cx as b:
        return b  # tpyc: warning(/copies Box into owned storage.*'b' is bound to existing storage/)


# field store + back edge: the store's move gives way to a later borrowing binding
def field_back(hd: Holder, ws: list[Box]) -> None:
    b = Box(1)
    i = 0
    while i < len(ws):
        if i == 1:
            hd.b = b  # tpyc: warning(/copies Box into field; .*'b' is bound to existing storage/)
            return
        b = ws[i]
        i += 1


class Store:
    f: Box
    t: tuple[Box, int32]

    def __init__(self) -> None:
        self.f = Box(0)
        self.t = (Box(0), 0)

    # method: a `self` field store before a back-edge borrowing binding
    def put(self, ws: list[Box]) -> None:
        b = Box(1)
        i = 0
        while i < len(ws):
            if i == 1:
                self.f = b  # tpyc: warning(/copies Box into field; .*'b' is bound to existing storage/)
                return
            b = ws[i]
            i += 1

    def t_head(self) -> int32:
        a, k = self.t
        return a.xs[0]

    # method: a tuple literal's member stored in a field, in the field's words
    def put_tuple(self, ws: list[Box]) -> None:
        b = Box(1)
        i = 0
        while i < len(ws):
            if i == 1:
                self.t = (b, i)  # tpyc: warning(/copies Box into field \(tuple element 0\).*'b' is bound/)
                return
            b = ws[i]
            i += 1


def collect(source: Iterable[Own[Box]]) -> Own[list[Box]]:
    out: list[Box] = []
    for x in source:
        out.append(x)
    return out


# `Iterable[Own[T]]` argument + back edge: the elements copy, in the argument's words
def iter_back(ws: list[list[Box]]) -> Own[list[Box]]:
    xs: list[Box] = [Box(1)]
    i = 0
    while i < len(ws):
        if i == 1:
            return collect(xs)  # tpyc: warning(/copies Box elements; use copy_iter\(\).*'xs' is bound/)
        xs = ws[i]
        i += 1
    return []


# builtin collectors + back edge: `extend` and `list(...)` copy the elements, warned
def extend_back(ws: list[list[Box]], out: list[Box]) -> None:
    xs: list[Box] = [Box(1)]
    i = 0
    while i < len(ws):
        if i == 1:
            out.extend(xs)  # tpyc: warning(/copies Box elements; .*'xs' is bound/)
            return
        xs = ws[i]
        i += 1


def list_back(ws: list[list[Box]]) -> Own[list[Box]]:
    xs: list[Box] = [Box(1)]
    i = 0
    while i < len(ws):
        if i == 1:
            return list(xs)  # tpyc: warning(/copies Box elements; .*'xs' is bound/)
        xs = ws[i]
        i += 1
    return []


# async: a consuming call on a borrowed parameter copies; an owning local moves
async def async_recv(w: Box) -> Own[Box]:
    return w.finish()  # tpyc: warning(/copies Box into owned storage/)


async def async_owning() -> Own[Cell]:
    b = Cell(3)
    b.n += 1
    return b.finish()  # tpyc: ok


# walrus: a capture of existing storage makes the name non-owning
def walrus(hd: Holder) -> Own[Box]:
    if (b := hd.b) is hd.b:
        return b  # tpyc: warning(/copies Box into owned storage.*'b' is bound to existing storage/)
    return Box(0)


@overload
def ov(h: Pair, c: bool, tag: int32) -> Own[Box]: ...


@overload
def ov(h: Pair, c: bool, tag: None) -> Own[Box]: ...


# overload-synthesized body: a tuple element one binding borrows copies in each
def ov(h: Pair, c: bool, tag: int32 | None) -> Own[Box]:
    p = make_pair(1)
    if c:
        p = h.pair
    return p[0]  # tpyc: warning(/copies Box into owned storage.*'p' is bound to existing storage/)


# `Own` parameter rebound by an unpack: copied into its own storage, then moved
def own_param_unpack(o: Own[Box], hd: Holder, c: bool) -> Own[Box]:
    if c:
        o, k = (hd.b, 1)  # tpyc: warning(/copies Box into owned storage/)
    return o  # tpyc: ok


# `Own[T] | None` return: a forward borrowing binding makes the name non-owning
def opt_ret_fwd(w: Box, c: bool) -> Own[Box] | None:
    b = w
    if c:
        b = Box(1)
    return b  # tpyc: warning(/copies Box into owned storage.*'b' is bound to existing storage/)


# `Own[T] | None` return before a back-edge borrowing binding: settled into the copy
def opt_ret_back(ws: list[Box]) -> Own[Box] | None:
    b = Box(1)
    i = 0
    while i < len(ws):
        if i == 1:
            return b  # tpyc: warning(/copies Box into owned storage.*'b' is bound to existing storage/)
        b = ws[i]
        i += 1
    return None


# `Own[T] | None` return of an owning local: moves
def opt_ret_own(c: bool) -> Own[Cell] | None:
    b = Cell(1)
    if c:
        b = Cell(2)
    return b  # tpyc: ok


def opt_fwd_head(w: Box) -> int32:
    o = opt_ret_fwd(w, False)
    if o is None:
        return -1
    b: Box = o
    return b.xs[0]


def opt_back_head(ws: list[Box]) -> int32:
    o = opt_ret_back(ws)
    if o is None:
        return -1
    b: Box = o
    return b.xs[0]


def opt_own_n() -> int32:
    o = opt_ret_own(True)
    if o is None:
        return -1
    return o.n


# `copy(b)` before a back-edge borrowing binding: the copy is needed, no advice
def copy_back(ws: list[Box]) -> Own[Box]:
    b = Box(1)
    i = 0
    while i < len(ws):
        if i == 1:
            return copy(b)  # tpyc: ok
        b = ws[i]
        i += 1
    return Box(0)


# `copy(b)` of an owning local at its last use: the move would do
def copy_owning() -> Own[Box]:
    b = Box(1)
    return copy(b)  # tpyc: warning(/unnecessary copy\(\) -- 'b' is at its last use/)


def main() -> None:
    w = Box(7)
    r = ret(w, False)
    print("ret", w.xs, r.xs)
    print("own_arg", own_arg(w, False), w.xs)
    xs = append(w, False)
    print("append", w.xs, xs[0].xs)
    xs = literal(w, False)
    print("literal", w.xs, xs[0].xs)
    r = receiver(w, False)
    print("receiver", w.xs, r.xs)
    r = param_receiver(w)
    print("param_receiver", w.xs, r.xs)
    print("keep", keep(True).n)
    print("handover", handover().n)
    r = own_param(Box(1), w)
    print("own_param", w.xs, r.xs)
    h = Pair()
    r = elem_return(h, True)
    print("elem_return", head(h), r.xs)
    print("elem_arg", elem_arg(Box(3), w, False), w.xs)
    h = Pair()
    t = whole(h, True)
    t0 = t[0]
    print("whole", head(h), t0.xs)
    l0, l1 = whole_literal(Box(3), w, False)
    print("whole_literal", w.xs, l0.xs)
    u0, u1 = opt_whole(None, True)
    print("opt_whole", u0 is None, u1.xs)
    ws = [Box(4), Box(5)]
    r = back_edge(ws)
    print("back_edge", ws[0].xs, r.xs)
    h = Pair()
    r = elem_back_edge(h, 3)
    print("elem_back_edge", head(h), r.xs)
    src = [Box(7)]
    out = loop_back_edge(src)
    print("loop_back_edge", len(src), len(src[0].xs), len(out))
    m = M()
    r = m.ret(w, False)
    print("method ret", w.xs, r.xs)
    print("method own_arg", m.own_arg(w, False), w.xs)
    r = m.receiver(w, False)
    print("method receiver", w.xs, r.xs)
    hd = ctor(w, False)
    hb = hd.b
    print("ctor", w.xs, hb.xs)
    r = nested(w, False)
    print("nested", w.xs, r.xs)
    for n in gen(w):
        print("gen", n, w.xs)
    r = matcher(w)
    print("match", w.xs, r.xs)
    r = tried(w, False)
    print("try", w.xs, r.xs)
    try:
        r = er(w, False)
        print("error_return", w.xs, r.xs)
    except Fail:
        print("error_return failed")
    cx = Ctx()
    r = with_as(cx)
    ci = cx.inner
    print("with", ci.xs, r.xs)
    ws = [Box(4), Box(5)]
    r = fin_back(ws)
    print("fin_back", ws[0].xs, r.xs)
    f0, fk = fin_tup_back(ws)
    print("fin_tup_back", ws[0].xs, f0.xs, fk)
    print("nested_receiver", nested_receiver().n)
    pw = (w, 1)
    r = unpacked(pw, False)
    print("unpacked", w.xs, r.xs)
    ps = [(Box(3), 1)]
    r = for_head(ps)
    print("for_head", len(ps), r.xs)
    r = ternary(w, True)
    print("ternary", w.xs, r.xs)
    print("opt_own", opt_own(Cell(4)))
    print("tup_own", tup_own((Cell(5), 1)))
    print("ro_own", ro_own(Cell(6)))
    r = explicit(w, False)
    print("explicit", w.xs, r.xs)
    sn = second_name(Cell(1))
    print("second_name", sn.n)
    hf = Holder(Box(0))
    ws = [Box(4), Box(5)]
    field_back(hf, ws)
    fb = hf.b
    print("field_back", ws[0].xs, fb.xs)
    st = Store()
    st.put(ws)
    sf = st.f
    print("put", ws[0].xs, sf.xs)
    st.put_tuple(ws)
    print("put_tuple", ws[0].xs, st.t_head())
    wss = [[Box(4)], [Box(5)]]
    cl = iter_back(wss)
    print("iter_back", len(wss[0]), cl[0].xs)
    eout: list[Box] = []
    extend_back(wss, eout)
    print("extend_back", len(wss[0]), eout[0].xs)
    lb = list_back(wss)
    print("list_back", len(wss[0]), lb[0].xs)
    r = asyncio.run(async_recv(w))
    print("async_recv", w.xs, r.xs)
    print("async_owning", asyncio.run(async_owning()).n)
    r = walrus(hf)
    wb = hf.b
    print("walrus", wb.xs, r.xs)
    h = Pair()
    r = ov(h, True, 1)
    print("ov int", head(h), r.xs)
    r = ov(h, True, None)
    print("ov none", head(h), r.xs)
    r = own_param_unpack(Box(1), hf, True)
    ub = hf.b
    print("own_param_unpack", ub.xs, r.xs)
    print("opt_ret_fwd", w.xs, opt_fwd_head(w))
    rws = [Box(4), Box(5)]
    print("opt_ret_back", rws[0].xs, opt_back_head(rws))
    print("opt_ret_own", opt_own_n())
    r = copy_back(rws)
    print("copy_back", rws[0].xs, r.xs)
    r = copy_owning()
    print("copy_owning", r.xs)
    r = recv_lent()
    print("recv_lent", r.xs)
    print("recv_owning", recv_owning().n)
    rp = repeat(w, False)
    print("repeat", w.xs, len(rp), rp[0].xs)


main()
