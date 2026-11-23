# A str literal with an embedded NUL at a deduced `Iterable[char]` slot, a
# `*args` pack of references under a conditional operand, and a forwarded
# protocol-parameter alias read inside a nested def.
import asyncio
from typing import Iterable, Iterator
from tpy import Own, char, int32


def cnt(xs: Iterable[char]) -> int32:
    n = 0
    for x in xs:
        n += 1
    return n


def gcount(xs: Iterable[char]) -> Iterator[int32]:
    for x in xs:
        yield 1


async def ctotal(xs: Iterable[char]) -> int32:
    await asyncio.sleep(0)
    n = 0
    for x in xs:
        n += 1
    return n


def nul_gen() -> Iterator[int32]:
    # a literal holding a NUL, kept by a generator head
    for x in gcount('a\x00b'):  # tpyc: ok
        yield x


async def nul_co() -> int32:
    # ... and by an awaited coroutine
    return await ctotal('a\x00bc')  # tpyc: ok


class P:
    def __init__(self, n: int32) -> None:
        self.n = n


def sl(*args: list[int32]) -> int32:
    s = 0
    for a in args:
        s += len(a)
    return s


def sd(*args: dict[str, int32]) -> int32:
    s = 0
    for a in args:
        s += len(a)
    return s


def sp(*args: P) -> int32:
    s = 0
    for a in args:
        s += a.n
    return s


def mk(n: int32) -> Own[list[int32]]:
    print("restored mk", n)
    return [n] * n


def pack_tern(c: bool) -> int32:
    # list / dict literal, record and call elements of a readonly pack,
    # built only when the arm is taken
    a = 5 if c else sl([1, 2, 3])  # tpyc: ok
    b = 5 if c else sd({"a": 1, "b": 2})
    d = 5 if c else sp(P(4), P(5))
    e = 5 if c else sl(mk(2))
    return a * 1000 + b * 100 + d * 10 + e


def pack_short(c: bool) -> bool:
    # ... under `and` / `or`
    x = c and sl([1, 2]) > 1  # tpyc: ok
    y = c or sd({"a": 1}) > 0
    w = c or sl(mk(3)) > 0
    return x and y and w


def pack_gen(c: bool) -> Iterator[int32]:
    # ... in a generator's yield
    yield 5 if c else sl([1, 2, 3])  # tpyc: ok
    yield 8 if c else sl(mk(1))
    yield 5 if c else sd({"a": 1})  # tpyc: ok
    yield 6 if c else sp(P(4), P(5))  # tpyc: ok


async def pack_co(c: bool) -> int32:
    # ... in a coroutine
    await asyncio.sleep(0)
    a = 5 if c else sl([1, 2, 3], mk(2))  # tpyc: ok
    await asyncio.sleep(0)
    b = 5 if c else sd({"a": 1, "b": 2})  # tpyc: ok
    d = 5 if c else sp(P(4), P(5))  # tpyc: ok
    await asyncio.sleep(0)
    return a * 100 + b * 10 + d


def fwd_gen(it: Iterable[int32]) -> Iterator[int32]:
    # a forwarded alias of a protocol parameter read in a nested def
    xs = it

    def f() -> int32:
        return sum(xs)  # tpyc: ok
    yield f()


async def fwd_co(it: Iterable[int32]) -> int32:
    # ... of a coroutine, across a suspension
    xs = it

    def f() -> int32:
        return sum(xs)  # tpyc: ok
    await asyncio.sleep(0)
    return f()


def main() -> None:
    print("restored nul", cnt('a\x00b'), sum(nul_gen()), asyncio.run(nul_co()))
    t = pack_tern(True)
    f = pack_tern(False)
    print("restored pack tern", t, f)
    s = pack_short(True)
    s2 = pack_short(False)
    print("restored pack short", s, s2)
    g = list(pack_gen(True))
    g2 = list(pack_gen(False))
    print("restored pack gen", g, g2)
    k = asyncio.run(pack_co(True))
    k2 = asyncio.run(pack_co(False))
    print("restored pack co", k, k2)
    a = [1, 2, 3]
    print("restored fwd gen", list(fwd_gen(a)))
    a.append(4)
    print("restored fwd co", asyncio.run(fwd_co(a)))
