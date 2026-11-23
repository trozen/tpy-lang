# A head keeps a fresh argument at every lending parameter (`str` view,
# `Span`), under a condition and across a suspension.
import asyncio
from typing import Iterator
from tpy import Own, Span, StrView, int32, readonly


class Src:
    xs: list[int32]

    def __init__(self, n: int32) -> None:
        self.xs = [n, n + 1]


class H:
    v: list[int32]
    n: int32

    def __init__(self, k: int32) -> None:
        # member-init: a comprehension clause source keeps its fresh argument
        # in the comprehension's own block
        self.v = [x * 2 for x in walk(Src(k))]  # tpyc: ok
        self.n = total([x for x in walk(Src(k))])  # tpyc: ok


def mks(n: int32) -> str:
    return "view-" + str(n)


def sp(s: str) -> StrView:
    return s


def mkl(n: int32) -> Own[list[int32]]:
    return [n, n + 1]


def spn(xs: Span[readonly[int32]]) -> Span[readonly[int32]]:
    return xs


def walk(s: Src) -> list[int32]:
    return s.xs


def pick(xs: list[int32]) -> list[int32]:
    return xs


def total(xs: list[int32]) -> int32:
    return sum(xs)


def first(xs: list[int32]) -> int32:
    return xs[0]


def plain(c: bool, other: list[int32]) -> None:
    # str view parameter: the fresh str the view points into
    for ch in sp(mks(8)):  # tpyc: ok
        print("view str for", ch)
    # Span parameter built over a fresh list
    for v in spn(mkl(3)):  # tpyc: ok
        print("view span", v)
    # an operand evaluated only under a condition is kept as well
    for x in pick(walk(Src(1)) if c else other):  # tpyc: ok
        print("cond head", x)
    # a walrus target the head binds is read after the loop
    for y in walk(Src(m := 5)):  # tpyc: ok
        print("walrus head", y)
    print("walrus after", m)
    h = H(4)
    print("member init", h.v, h.n)


def gen() -> Iterator[int32]:
    for ch in sp(mks(9)):  # tpyc: ok
        yield len(ch)
    for v in spn(mkl(20)):  # tpyc: ok
        yield v
    # match subject in a generator: the argument lives on the frame
    match first(walk(Src(40))):  # tpyc: ok
        case 40:
            yield 40
        case _:
            yield 0


def side(n: int32) -> int32:
    return n * 10


def pair(a: int32, xs: list[int32]) -> int32:
    return a + sum(xs)


def order() -> None:
    # the comprehension source's kept argument is built inside the
    # comprehension's block, where its argument is evaluated (argument order
    # is a deliberate divergence: the render pins it, the output does not
    # observe it)
    print("comp order", pair(side(5), [x for x in walk(Src(6))]))  # tpyc: ok


async def agen() -> int32:
    s = 0
    # the kept argument outlives the suspension in the body
    for x in walk(Src(60)):  # tpyc: ok
        await asyncio.sleep(0)
        s += x
    return s


def main() -> None:
    plain(True, [9])
    plain(False, [9])
    print("gen", list(gen()))
    order()
    junk = [Src(99) for _ in range(5)]
    print("async", asyncio.run(agen()), len(junk))
