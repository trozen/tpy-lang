# An async `return` converts its value as a sync return does (borrows alias,
# owned payloads copy warned or move), one section per position.
import asyncio

from typing import Self

from tpy import int32, Own, String, ValueType


class Box:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class Holder:
    b: Box
    opt: Box | None

    def __init__(self, v: int32) -> None:
        self.b = Box(v)
        self.opt = Box(v + 1)

    def get(self) -> Box:
        return self.b

    # receiver: a consuming method's `self` at a by-value Optional payload
    # moves out only after the finally that writes it
    def take(self: Own[Self]) -> Own[Self] | None:
        try:
            return self  # tpyc: ok
        finally:
            self.b.v += 100

    async def take_async(self: Own[Self]) -> Own[Self] | None:
        try:
            return self  # tpyc: ok
        finally:
            self.b.v += 100

    # method: a field of self at a borrow payload aliases it
    async def inner(self) -> Box:
        return self.b  # tpyc: ok

    # method: a field of self at an owned payload copies it, warned
    async def snapshot(self) -> Own[Box]:
        return self.b  # tpyc: warning(/copies Box into owned storage/)


def mk(v: int32) -> Own[Box]:
    print("mk", v)
    return Box(v)


def poke(b: Box) -> int32:
    b.v += 1
    return b.v


# free fn: a parameter's field at a borrow payload
async def field_of(h: Holder) -> Box:
    return h.b  # tpyc: ok


# free fn: a container element at a borrow payload
async def element_of(xs: list[Box]) -> Box:
    return xs[0]  # tpyc: ok


# free fn: an accessor's borrowed result at a borrow payload
async def accessor_of(h: Holder) -> Box:
    return h.get()  # tpyc: ok


# free fn: a ternary over two parameters at a borrow payload
async def either(a: Box, b: Box, first: bool) -> Box:
    return a if first else b  # tpyc: ok


# free fn: None and a field at a pointer Optional payload
async def maybe(h: Holder, some: bool) -> Box | None:
    if some:
        return h.opt  # tpyc: ok
    return None  # tpyc: ok


# free fn: a field at an owned payload copies it, warned
async def owned_copy(h: Holder) -> Own[Box]:
    return h.b  # tpyc: warning(/copies Box into owned storage/)


# free fn: owned list literals, empty and repeated
async def empty() -> Own[list[int32]]:
    return []  # tpyc: ok


async def repeated(x: int32) -> Own[list[int32]]:
    return [x] * 3  # tpyc: ok


# free fn: a literal at a wider payload
async def big() -> int:
    return 42  # tpyc: ok


# free fn: a fresh record at a mutable reference parameter takes a
# temporary hoisted ahead of the return
async def poked(n: int32) -> int32:
    await asyncio.sleep(0)
    return poke(Box(n))  # tpyc: ok


# free fn: an open T parameter, instantiated at a value and a reference type
async def ident[T](x: T) -> T:
    return x  # tpyc: ok


class Cell[T]:
    x: T

    def __init__(self, x: Own[T]) -> None:
        self.x = x

    def get(self) -> T:
        return self.x

    def at(self, index: int32) -> T:
        return self.x


def same[T](v: T) -> T:
    return v


# open T: a call result lending its parameter receiver aliases it
async def unwrap[T](c: Cell[T]) -> T:
    return c.get()  # tpyc: ok


# open T: a literal argument the result does not lend from
async def indexed[T](c: Cell[T]) -> T:
    return c.at(0)  # tpyc: ok


# open T: an open-T call over an open-T call over the parameter
async def nested_open[T](c: Cell[T]) -> T:
    return same(c.get())  # tpyc: ok


# open T (sync twin): a literal argument the result does not lend from
def sync_indexed[T](c: Cell[T]) -> T:
    return c.at(0)  # tpyc: ok


# open T (sync twin): an open-T call over an open-T call over the parameter
def sync_nested_open[T](c: Cell[T]) -> T:
    return same(c.get())  # tpyc: ok


# try/finally (inline): the finally reads the local the returned tuple holds
async def tup_finally() -> tuple[Own[Box], int32]:
    b = Box(3)
    try:
        return (b, 1)  # tpyc: ok
    finally:
        print("tup_finally fin", b.v)


# try/finally (suspending): the same with an await inside the finally
async def tup_finally_await() -> tuple[Own[Box], int32]:
    b = Box(4)
    try:
        # The pending return is copied before the suspending finally reads b.
        return (b, 1)  # tpyc: warning(/copies Box into owned storage \(tuple element 0\)/)
    finally:
        await asyncio.sleep(0)
        print("tup_finally_await fin", b.v)


# try/finally (inline): the returned owned local is read by the finally
async def own_finally() -> Own[Box]:
    b = Box(7)
    try:
        return b  # tpyc: ok
    finally:
        print("own_finally fin", b.v)


# try/finally (suspending): an await inside the finally
async def own_finally_await() -> Own[Box]:
    b = Box(8)
    try:
        # The pending return is copied before the suspending finally reads b.
        return b  # tpyc: warning(/copies Box into owned storage/)
    finally:
        await asyncio.sleep(0)
        print("own_finally_await fin", b.v)


# try/finally (inline): an owned parameter at a by-value Optional payload
# moves out only after the finally that writes it
async def param_finally(b: Own[Box]) -> Own[Box] | None:
    try:
        return b  # tpyc: ok
    finally:
        b.v += 100


# try/finally (sync twin): the same at a sync return
def sync_param_finally(b: Own[Box]) -> Own[Box] | None:
    try:
        return b  # tpyc: ok
    finally:
        b.v += 100


def show_sync_param_finally() -> None:
    spf = sync_param_finally(Box(10))
    print("sync_param_finally", spf.v if spf is not None else -1)


# try/finally (inline): a body local at a by-value Optional payload
async def local_opt_finally() -> Own[Box] | None:
    b = Box(12)
    try:
        return b  # tpyc: ok
    finally:
        b.v += 100


def sync_local_opt_finally() -> Own[Box] | None:
    b = Box(13)
    try:
        return b  # tpyc: ok
    finally:
        b.v += 100


# try/finally (sync): a body local at an owned union payload; the async
# twin is refused by the union payload gate
def own_union_finally() -> Own[Box | int32]:
    b = Box(14)
    try:
        return b  # tpyc: ok
    finally:
        b.v += 100


class Sub(Box):
    def __init__(self, v: int32) -> None:
        super().__init__(v)


# try/finally (sync): a subclass local converts into its base member after
# the finally that writes it
def sub_union_finally() -> Own[Box | int32]:
    s = Sub(15)
    try:
        return s  # tpyc: warning(/upcast narrows 'Sub' to 'Box'/)
    finally:
        s.v += 100


# try/finally (sync): an owned str parameter at a value union payload
def str_param_union(s: Own[str]) -> str | int32:
    try:
        return s  # tpyc: ok
    finally:
        print("str_param_union fin", s)


# try/finally (sync): an int32 local, the union's non-first member
def int_union_finally() -> str | int32:
    n: int32 = 16
    try:
        return n  # tpyc: ok
    finally:
        print("int_union_finally fin", n)


# try/finally (sync): a String local is no member of `str | int32`, so the
# return takes the eager capture
def string_union_finally() -> str | int32:
    s = String("ab")
    try:
        return s  # tpyc: ok
    finally:
        print("string_union_finally fin", s)


# try/finally (sync): declared union and Optional locals keep the eager
# capture
def declared_union_finally() -> str | int32:
    u: str | int32 = "du"
    try:
        return u  # tpyc: ok
    finally:
        print("declared_union_finally fin", u)


def declared_opt_finally() -> str | None:
    o: str | None = "do"
    try:
        return o  # tpyc: ok
    finally:
        print("declared_opt_finally fin", o)


class V[T: ValueType]:
    # generic method: an owned value parameter at an open-T Optional payload
    def f(self, x: Own[T]) -> T | None:
        try:
            return x  # tpyc: ok
        finally:
            print("V.f finally", x)


def show_sync_finally() -> None:
    slo = sync_local_opt_finally()
    if slo is not None:
        print("sync_local_opt_finally", slo.v)
    ou = own_union_finally()
    if isinstance(ou, Box):
        print("own_union_finally", ou.v)
    su = sub_union_finally()
    if isinstance(su, Box):
        print("sub_union_finally", su.v)
    r_str_param_union = str_param_union("sp")
    print("str_param_union", r_str_param_union)
    r_int_union_finally = int_union_finally()
    print("int_union_finally", r_int_union_finally)
    r_string_union_finally = string_union_finally()
    print("string_union_finally", r_string_union_finally)
    r_declared_union_finally = declared_union_finally()
    print("declared_union_finally", r_declared_union_finally)
    r_declared_opt_finally = declared_opt_finally()
    print("declared_opt_finally", r_declared_opt_finally)
    vf = V[str]().f("hello")
    print("V.f", vf)
    h = Holder(18)
    tk = h.take()
    if tk is not None:
        print("take", tk.b.v)


# try/finally: a side-effecting temporary runs before the finally
async def temp_finally(n: int32) -> int32:
    try:
        return poke(mk(n))  # tpyc: ok
    finally:
        print("temp_finally fin")


# try/finally (suspending): a borrow payload ahead of an await in finally
async def borrow_finally(h: Holder) -> Box:
    try:
        return h.b  # tpyc: ok
    finally:
        await asyncio.sleep(0)
        h.b.v += 100


class Flag:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __bool__(self) -> bool:
        return self.n != 0


def make_flag() -> Own[Flag]:
    return Flag(10)


# select: an `or` with a fresh left operand at an owned payload evaluates it
# once into a temporary hosted ahead of the return
async def pick_flag() -> Own[Flag]:
    await asyncio.sleep(0)
    return Flag(0) or make_flag()  # tpyc: ok


# nested leaf: a return inside a non-suspending branch
async def nested(h: Holder, xs: list[Box], k: int32) -> Box:
    await asyncio.sleep(0)
    if k > 0:
        return xs[0]  # tpyc: ok
    return h.b


# match arm: each arm returns a different borrow
async def by_match(h: Holder, xs: list[Box], k: int32) -> Box:
    await asyncio.sleep(0)
    match k:
        case 0:
            return h.b  # tpyc: ok
        case _:
            return xs[0]


async def main() -> None:
    h = Holder(1)
    (await field_of(h)).v = 10
    print("field_of", h.b.v)
    r = await field_of(h)
    r.v = 11
    print("field_of bound", h.b.v)

    xs = [Box(2)]
    (await element_of(xs)).v = 20
    print("element_of", xs[0].v)
    e = await element_of(xs)
    e.v = 21
    print("element_of bound", xs[0].v)

    (await accessor_of(h)).v = 30
    print("accessor_of", h.b.v)
    g = await accessor_of(h)
    g.v = 31
    print("accessor_of bound", h.b.v)

    a = Box(4)
    (await either(a, h.b, True)).v = 40
    print("either", a.v)
    t = await either(a, h.b, False)
    t.v = 41
    print("either bound", h.b.v)

    m = await maybe(h, True)
    if m is not None:
        m.v = 50
    print("maybe", h.opt.v if h.opt is not None else -1)
    print("maybe none", await maybe(h, False) is None)

    # The owned copy is warned: its value is printed, not written through.
    c = await owned_copy(h)
    print("owned_copy", c.v)

    print("empty", await empty())
    print("repeated", await repeated(4))
    print("big", await big())
    print("poked", await poked(5))

    print("ident value", await ident(7))
    n = await ident(h.b)
    n.v = 60
    print("ident ref", h.b.v)

    (await h.inner()).v = 70
    print("method inner", h.b.v)
    s = await h.snapshot()
    print("method snapshot", s.v)

    cb = Cell(Box(3))
    (await unwrap(cb)).v = 61
    print("unwrap", cb.x.v)
    u = await unwrap(cb)
    u.v = 62
    print("unwrap bound", cb.x.v)
    print("unwrap value", await unwrap(Cell(8)))
    (await indexed(cb)).v = 63
    print("indexed", cb.x.v)
    iv = await indexed(cb)
    iv.v = 64
    print("indexed bound", cb.x.v)
    sync_indexed(cb).v = 67
    print("sync_indexed", cb.x.v)
    si = sync_indexed(cb)
    si.v = 68
    print("sync_indexed bound", cb.x.v)
    sync_nested_open(cb).v = 69
    print("sync_nested_open", cb.x.v)
    sn = sync_nested_open(cb)
    sn.v = 70
    print("sync_nested_open bound", cb.x.v)
    (await nested_open(cb)).v = 65
    print("nested_open", cb.x.v)
    no = await nested_open(cb)
    no.v = 66
    print("nested_open bound", cb.x.v)

    tbox, tv = await tup_finally()
    print("tup_finally", tbox.v, tv)
    tbox2, tv2 = await tup_finally_await()
    print("tup_finally_await", tbox2.v, tv2)
    print("own_finally", (await own_finally()).v)
    print("own_finally_await", (await own_finally_await()).v)
    pf = await param_finally(Box(9))
    print("param_finally", pf.v if pf is not None else -1)
    show_sync_param_finally()
    lo = await local_opt_finally()
    print("local_opt_finally", lo.v if lo is not None else -1)
    show_sync_finally()
    ht = Holder(17)
    tk = await ht.take_async()
    print("take_async", tk.b.v if tk is not None else -1)
    print("temp_finally", await temp_finally(6))
    bf = await borrow_finally(h)
    print("borrow_finally", bf.v, h.b.v)

    print("pick_flag", (await pick_flag()).n)

    (await nested(h, xs, 1)).v = 80
    print("nested", xs[0].v)
    (await by_match(h, xs, 0)).v = 90
    print("by_match", h.b.v)


asyncio.run(main())
