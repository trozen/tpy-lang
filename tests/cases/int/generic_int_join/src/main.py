# A type parameter bound directly by fixed ints of different widths binds to
# their join (the wider one); max/min are generic over AnyFixedInt, and
# list/set/dict literal peers of mixed widths join the same way.
from __future__ import annotations
import asyncio
from typing import Iterator
from tpy import int8, int16, int32, int64, uint8, uint32, dispatch


def pick[T](x: T, y: T) -> T:
    return y


def last3[T](x: T, y: T, z: T) -> T:
    return z


@dispatch
def which(x: int64, y: int64) -> str:
    return "concrete"


@dispatch
def which[T](x: T, y: T) -> str:
    return "generic"


# the same pair declared generic-first: the concrete one still wins
@dispatch
def which_g[T](x: T, y: T) -> str:
    return "generic"


@dispatch
def which_g(x: int64, y: int64) -> str:
    return "concrete"


# which fixed width a value has, for the same-width max/min pins
@dispatch
def width(x: int8) -> str:
    return "int8"


@dispatch
def width(x: uint8) -> str:
    return "uint8"


@dispatch
def width(x: int32) -> str:
    return "int32"


@dispatch
def width(x: int64) -> str:
    return "int64"


def free_function() -> None:
    a = int64(5)
    b = int32(100000)
    # user generic at (int64, int32) in both orders: T is int64
    p = pick(a, b)  # tpyc: type(int64)
    q = pick(b, a)  # tpyc: type(int64)
    print("free pick", p, q, a * p * p)
    # three bindings, joined over all of them in any order: T is int16
    r = last3(int8(-1), uint8(200), int16(300))  # tpyc: type(int16)
    s = last3(int16(300), int8(-1), uint8(200))  # tpyc: type(int16)
    print("free last3", r, s)
    # a concrete overload wins the conversion tie against the joined generic
    print("free which", which(int32(1), int64(2)))
    # declared generic-first: the concrete overload still wins the tie
    print("free which_g", which_g(int32(1), int64(2)))
    # max/min compute in the wider fixed type, not in int
    m = max(int32(5), int64(100000))  # tpyc: type(int64)
    k = max(b, a)  # tpyc: type(int64)
    n = min(a, b, int8(3))  # tpyc: type(int64)
    print("free max/min", m * m * m, a * k * k, n)
    # three arguments at three widths compute in the widest
    m3 = max(int8(3), b, a)  # tpyc: type(int64)
    print("free max3", a * m3 * m3)
    # a same-width pair keeps its width, sub-int32 ones included (the old
    # int32 result was an accident of the stub having only an int32 overload)
    su = max(uint8(200), uint8(100))  # tpyc: type(uint8)
    si = max(int8(100), int8(90))  # tpyc: type(int8)
    sl = max(a, int64(7))  # tpyc: type(int64)
    print("free same-width", width(su), ~su, width(si), si + int8(20), width(sl))
    # a pair with no common fixed type still takes the int overload
    w = max(int32(1), uint32(2))  # tpyc: type(int)
    print("free no-common", w)
    # range over mixed bounds counts in the wider type
    total = int64(0)
    for i in range(b, a + 100003):  # tpyc: type(int64)
        total += i * i * i
    print("free range", total)


def literals() -> None:
    a = int64(5)
    b = int32(100000)
    xs = [a, b]  # tpyc: type(/int64/)
    ss = {a, b}  # tpyc: type(set[int64])
    ks = {a: 1, b: 2}  # tpyc: type(dict[int64, int32])
    vs = {1: a, 2: b}  # tpyc: type(dict[int32, int64])
    # the annotated path, unchanged
    ys: list[int64] = [a, b, 7]
    # annotated set / dict literals take the narrower peer the same way
    s64: set[int64] = {a, b}
    d64: dict[str, int64] = {"a": a, "b": b}
    xs[1] = a * xs[1] * b
    print("literals", xs, sorted(ss), ks[b], a * vs[2] * b, ys)
    print("annotated", [a * e * e for e in sorted(s64)], a * d64["b"] * b)


class Meter:
    def __init__(self, base: int64) -> None:
        self.base = base

    def either[T](self, x: T, y: T) -> T:
        return y

    def top(self, v: int32) -> int64:
        # method position: the field and the narrower parameter join
        return self.base * max(self.base, v) * v  # tpyc: ok


def gen(a: int64, b: int32) -> Iterator[int64]:
    # generator position
    yield pick(b, a) * b  # tpyc: ok
    yield min(a, b)


async def coro(a: int64, b: int32) -> int64:
    await asyncio.sleep(0)
    # async position
    return a * max(a, b) * b  # tpyc: ok


def positions() -> None:
    a = int64(5)
    b = int32(100000)
    print("method", Meter(a).top(b))
    # a user generic method: T bound by (int64, int32) is int64
    e = Meter(a).either(a, b)  # tpyc: type(int64)
    print("generic method", a * e * e)
    print("generator", list(gen(a, b)))
    print("async", asyncio.run(coro(a, b)))
    small = [1, 2, 300000]
    # comprehension position
    print("comprehension", [a * max(x, a) * x for x in small])  # tpyc: ok


def pending() -> None:
    a = int64(5)
    # a literal-only local settles at the default int for the generic call
    p = 0
    print("pending", max(p, 5), max(p, a) * int32(100000) * int32(100000))


def main() -> None:
    free_function()
    literals()
    positions()
    pending()


main()
