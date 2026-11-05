# A ValueType record element read is its `__getitem__` call's by-value result: it
# passes, returns and stores as the explicit call `xs.__getitem__(i)` does, at every position.
# A ValueType is immutable, so its copies cannot be told from aliases; the reference
# sections (an Own-marked element, a class element) mutate after the boundary.
from __future__ import annotations
import asyncio
from typing import Iterator
from tpy import Own, ValueType, int32, uint64


class Color(ValueType):
    r: int32

    def __init__(self, r: int32) -> None:
        self.r = r

    def __eq__(self, other: Color) -> bool:
        return self.r == other.r

    def __hash__(self) -> uint64:
        return hash(self.r)


class Palette:
    c: Color

    def __init__(self, c: Color) -> None:
        self.c = c

    def __getitem__(self, i: int32) -> Color:
        return Color(self.c.r + i)


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class A:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class Slot:
    c: Color

    def __init__(self, c: Color) -> None:
        self.c = c


def mix(a: Color, b: Color) -> Color:
    return Color(a.r + b.r)


def take_opt(c: Color | None) -> int32:
    return -1 if c is None else c.r


def take_own(c: Own[Color]) -> int32:
    return c.r + 100


def bump(p: P) -> None:
    p.n += 1


def poke(a: A) -> None:
    a.x += 1


class Bag[T]:
    items: list[T]

    def __init__(self, items: Own[list[T]]) -> None:
        self.items = items

    def __getitem__(self, i: int32) -> T:
        return self.items[i]


def bag_own(b: Bag[Own[A]]) -> Own[A]:
    return b[0]  # tpyc: warning(/copies A into owned storage/)


def bag_own_call(b: Bag[Own[A]]) -> Own[A]:
    return b.__getitem__(0)  # tpyc: warning(/copies A into owned storage/)


def bag_ret(b: Bag[Own[A]]) -> A:
    return b[0]  # tpyc: ok


def mutate_plain_bag(b: Bag[A]) -> None:
    # the Own-less twin: a user generic __getitem__ -> T at T = A.
    poke(b[0])  # tpyc: ok
    print("plain bag mutate", b[0].x)


def mutate_bag(b: Bag[Own[A]]) -> None:
    # a user generic __getitem__ -> T at T = Own[A]: the stored element, as at a list.
    poke(b[0])  # tpyc: ok
    poke(b.__getitem__(0))  # tpyc: ok
    print("bag element mutate", b[0].x)
    # the owned results copy (warned); CPython hands back the element, so
    # only the returned objects print.
    c = bag_own(b)
    c.x = 50
    print("bag own result", c.x)
    d = bag_own_call(b)
    d.x = 60
    print("bag own call result", d.x)
    # a local bound to the element is the element itself.
    e = b[0]  # tpyc: ok
    e.x = 9
    print("bag local", b[0].x)
    # a `-> A` return hands back the element.
    r = bag_ret(b)
    r.x = 11
    print("bag return", b[0].x)
    # a tuple member borrows the element.
    t = (b[0], 5)  # tpyc: ok
    t[0].x = 12
    print("bag tuple", b[0].x)
    # a ternary arm is the element.
    z = b[1] if b[0].x > 0 else b[0]  # tpyc: ok
    z.x = 13
    print("bag ternary", b[1].x)


def mutate_own(xs: list[Own[A]]) -> None:
    # an Own-marked element at a mutating reference parameter is the element itself
    # (the explicit-call spelling: BUGS.md#explicit-getitem-call-field-read-rejects).
    poke(xs[0])  # tpyc: ok
    print("own element mutate", xs[0].x)


class Mixer:
    pts: list[Color]

    def __init__(self, pts: list[Color]) -> None:
        # constructor: element reads at a by-value parameter.
        self.pts = [mix(pts[0], pts[1])]  # tpyc: ok

    def run(self) -> int32:
        # method: a field's elements.
        return mix(self.pts[0], self.pts[0]).r  # tpyc: ok


def ret_elem(xs: list[Color]) -> Color:
    # return of an element.
    return xs[0]  # tpyc: ok


def ret_call(xs: list[Color]) -> Color:
    return xs.__getitem__(0)  # tpyc: ok


def gen(xs: list[Color]) -> Iterator[int32]:
    # generator: element reads at a by-value parameter.
    yield mix(xs[0], xs[1]).r  # tpyc: ok


async def coro(xs: list[Color]) -> int32:
    await asyncio.sleep(0)
    # async: element reads at a by-value parameter.
    return mix(xs[0], xs[1]).r  # tpyc: ok


# an Own-marked element at an owned result is the stored element, copied out
# and warned in both spellings (CPython hands back the element itself, so the
# section prints only the returned objects).
def own_elem(xs: list[Own[A]]) -> Own[A]:
    return xs[0]  # tpyc: warning(/copies A into owned storage/)


def own_elem_call(xs: list[Own[A]]) -> Own[A]:
    return xs.__getitem__(0)  # tpyc: warning(/copies A into owned storage/)


def free_fn(pts: list[Color]) -> None:
    lit = [Color(1), Color(2)]
    # free function: a list literal's elements, and the explicit-call twin.
    print("free literal", mix(lit[0], lit[1]).r, mix(lit.__getitem__(0), lit.__getitem__(1)).r)  # tpyc: ok
    # free function: a list parameter's elements.
    print("free param", mix(pts[0], pts[1]).r, mix(pts.__getitem__(0), pts.__getitem__(1)).r)  # tpyc: ok
    d = {k: Color(3) for k in [1.5, 2.5]}
    # a dict element under a float key (the explicit call over this receiver is
    # refused on the call side today: BUGS.md#getitem-call-float-key-rejects).
    print("dict float key", mix(d[1.5], d[2.5]).r)  # tpyc: ok
    p = Palette(Color(10))
    # a user __getitem__ -> Color.
    print("user getitem", mix(p[0], p[1]).r, mix(p.__getitem__(0), p.__getitem__(1)).r)  # tpyc: ok
    # an Optional parameter.
    print("optional param", take_opt(pts[0]), take_opt(pts.__getitem__(0)))  # tpyc: ok
    # an Own parameter takes a copy, never a move from the element.
    print("own param", take_own(pts[0]), take_own(pts.__getitem__(1)), pts[0].r, pts[1].r)  # tpyc: ok
    print("return", ret_elem(pts).r, ret_call(pts).r)
    s = Slot(Color(0))
    # a field store of an element.
    s.c = pts[1]  # tpyc: ok
    print("field store", s.c.r)
    slots = [Slot(Color(4))]
    # a ValueType field read off a list element.
    print("element field", mix(slots[0].c, slots[0].c).r)  # tpyc: ok
    out: list[Color] = []
    # push_back of an element.
    out.append(pts[0])  # tpyc: ok
    print("push_back", out[0].r)
    # comparison, membership and a ternary arm.
    print("compare", pts[0] == pts[1], pts[0] in pts, mix(pts[0] if len(pts) > 1 else pts[1], pts[0]).r)  # tpyc: ok
    # comprehension: element reads at a by-value parameter.
    print("comprehension", [mix(pts[i], pts[i]).r for i in range(2)])  # tpyc: ok
    print("method", Mixer(pts).run())
    for v in gen(pts):
        print("generator", v)
    print("async", asyncio.run(coro(pts)))
    owned: list[A] = [A(1)]
    # a borrowed list at a `list[Own[A]]` parameter copies its elements (warned).
    a = own_elem(owned)  # tpyc: warning(/copies A elements/)
    a.x = 5
    print("own element", a.x)
    b = own_elem_call(owned)
    b.x = 7
    print("own element call", b.x)
    mutate_own([A(1)])
    mutate_bag(Bag([A(1), A(10)]))
    mutate_plain_bag(Bag([A(1)]))
    ps = [P(1)]
    # inverse: a reference-record element at a reference parameter is the element itself.
    bump(ps[0])  # tpyc: ok
    print("reference element", ps[0].n)


def main() -> None:
    free_fn([Color(1), Color(2)])


main()
