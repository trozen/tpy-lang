# A tuple LITERAL member that arrives borrowed and lands in owned storage is
# copied, so it warns at every owning sink the scalar warns at: a yield into
# an Own element, a subscript store, a field store of a repack, a
# borrow-returning getter or a walrus, a return of one owned local twice; and
# the scalar twins of the walrus and of a mixed container ternary.
# Each section mutates the source after the boundary and reads the stored
# side, so a regression to silence still prints the copied value.
import asyncio
from typing import Iterator

from tpy import Own, int32


class C:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class H:
    q: tuple[int32, C]

    def __init__(self) -> None:
        self.q = (0, C(0))

    # method: a field repack of an owning tuple that stays live.
    def repack(self) -> None:
        t = mk(2)
        self.q = (t[0], t[1])  # tpyc: warning(/copies C into field \(tuple element 1\)/)
        t[1].v = 99
        print("method_repack", self.q[1].v)


class G:
    c: C

    def __init__(self) -> None:
        self.c = C(3)

    def get(self) -> C:
        return self.c


def mk(i: int32) -> tuple[int32, Own[C]]:
    return (i, C(i))


def show(tag: str, p: tuple[C | None, int32]) -> None:
    a, k = p
    if a is not None:
        print(tag, a.v)


# generator yield, borrowed param member into an Own element.
def yield_param(c: C) -> Iterator[tuple[int32, Own[C]]]:
    yield (1, c)  # tpyc: warning(/copies C into owned storage \(tuple element 1\)/)


# generator yield, owned local read again after the yield.
def yield_live_local() -> Iterator[tuple[int32, Own[C]]]:
    c = C(10)
    yield (1, c)  # tpyc: warning(/copies C into owned storage \(tuple element 1\)/)
    print("yield_live_local gen", c.v)


# generator yield, re-packing the elements of an owning tuple.
def yield_repack() -> Iterator[tuple[int32, Own[C]]]:
    t = (1, C(20))
    yield (t[0], t[1])  # tpyc: warning(/copies C into owned storage \(tuple element 1\)/)
    print("yield_repack gen", t[1].v)


# generator yield, mixed tuple: only the Own element copies.
def yield_mixed(a: C, b: C) -> Iterator[tuple[Own[C], C]]:
    yield (a, b)  # tpyc: warning(/copies C into owned storage \(tuple element 0\)/)


# return: the same owned local twice -- two copies where CPython returns one
# object twice.
def dup() -> tuple[Own[C], Own[C]]:
    b = C(1)
    return (b, b)  # tpyc: warning(/copies C into owned storage \(tuple element 0\)/) warning(/copies C into owned storage \(tuple element 1\)/)


# async return: the same two copies.
async def adup() -> tuple[Own[C], Own[C]]:
    b = C(1)
    return (b, b)  # tpyc: warning(/copies C into owned storage \(tuple element 0\)/) warning(/copies C into owned storage \(tuple element 1\)/)


async def run_adup() -> None:
    t = await adup()
    t[0].v = 9
    print("async_dup", t[1].v)


def walrus_member() -> None:
    # field store: a walrus member is the named object read again, so it
    # copies like the bare name.
    c = C(4)
    h = H()
    h.q = (1, (d := c))  # tpyc: warning(/copies C into field \(tuple element 1\)/)
    c.v = 44
    print("walrus_member", h.q[1].v, d.v)


# scalar return: the same walrus into an Own slot copies like `return c`.
def walrus_return(c: C) -> Own[C]:
    return (d := c)  # tpyc: warning(/copies C into owned storage; use copy/)


def takel(x: Own[list[int32]]) -> int32:
    x.append(7)
    return len(x)


def container_ternary() -> None:
    # Own argument: a mixed ternary of a list name and a fresh list lowers,
    # and the name arm is copied into the owned argument.
    xs = [1, 2]
    k = True
    print("container_ternary", takel(xs if k else [3]), len(xs))  # tpyc: warning(/copies list\[int32\] into owned storage/)


def getter_member() -> None:
    # field store: a borrow-returning getter hands back the field's storage.
    g = G()
    h = H()
    h.q = (1, g.get())  # tpyc: warning(/copies C into field \(tuple element 1\)/)
    g.c.v = 33
    print("getter_member", h.q[1].v)


def dict_setitem() -> None:
    # subscript store into a dict: the direct member copies.
    a = C(1)
    d: dict[int32, tuple[C | None, int32]] = {}
    d[0] = (a, 1)  # tpyc: warning(/copies C \| None into container \(tuple element 0\)/)
    a.v = 99
    show("dict_setitem", d[0])


def list_setitem() -> None:
    # subscript store into a list element.
    b = C(2)
    xs: list[tuple[C | None, int32]] = [(None, 0)]
    xs[0] = (b, 1)  # tpyc: warning(/copies C \| None into container \(tuple element 0\)/)
    b.v = 77
    show("list_setitem", xs[0])


def match_arm(tag: int32) -> None:
    # match arm body: the same subscript store.
    a = C(3)
    d: dict[int32, tuple[C | None, int32]] = {}
    match tag:
        case 1:
            d[0] = (a, 1)  # tpyc: warning(/copies C \| None into container \(tuple element 0\)/)
        case _:
            pass
    a.v = 98
    show("match_arm", d[0])


def try_body() -> None:
    # try body: the same subscript store.
    a = C(4)
    d: dict[int32, tuple[C | None, int32]] = {}
    try:
        d[0] = (a, 1)  # tpyc: warning(/copies C \| None into container \(tuple element 0\)/)
    except ValueError:
        pass
    a.v = 97
    show("try_body", d[0])


def main() -> None:
    p = C(1)
    for pr in yield_param(p):
        pr[1].v = 777
    print("yield_param", p.v)
    for pr in yield_live_local():
        pr[1].v = 777
    for pr in yield_repack():
        pr[1].v = 777
    a = C(1)
    b = C(2)
    for mx in yield_mixed(a, b):
        mx[0].v = 70
        mx[1].v = 80
    print("yield_mixed", a.v, b.v)
    t = dup()
    t[0].v = 9
    print("dup", t[1].v)
    asyncio.run(run_adup())
    getter_member()
    walrus_member()
    wc = C(5)
    wr = walrus_return(wc)
    wr.v = 55
    print("walrus_return", wc.v)
    container_ternary()
    dict_setitem()
    list_setitem()
    match_arm(1)
    try_body()
    h = H()
    h.repack()


main()
