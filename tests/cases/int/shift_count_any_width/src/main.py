# A shift count may be any fixed-width int of either sign and the result has
# the LEFT operand's type; the count is checked in its own type (a negative
# signed count raises ValueError). A literal left operand takes the count's
# type. `~` on an unsigned result shows its width.
from __future__ import annotations
from typing import Iterator
from tpy import int8, int16, int32, int64, uint8, uint32, uint64, Own, error_return, ReturnException
import asyncio


class Failed(Exception, ReturnException):
    pass


class Ctx:
    def __enter__(self) -> Ctx:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


class Bits:
    word: uint32

    # constructor: uint32 << uint8
    def __init__(self, w: uint32, s: uint8) -> None:
        shifted = w << s  # tpyc: type(uint32)
        self.word = shifted

    # method: uint32 >> int64, and a field aug-assign with an int8 count
    def take(self, k: int64, s: int8) -> uint32:
        top = self.word >> k  # tpyc: type(uint32)
        self.word <<= s  # tpyc: ok
        return top


# free function: int64 >> int32, int32 << int64, uint32 >> (int32 expr),
# int64 << uint8, a literal left operand, and a uint8 result's width
def free_fn(n: int64, k: int32, b: int32, a: int64, w: uint32, u8: uint8, k64: int64, k8: int8) -> None:
    r = n >> k  # tpyc: type(int64)
    s = b << a  # tpyc: type(int32)
    t = w >> (32 - k)  # tpyc: type(uint32)
    v = a << u8  # tpyc: type(int64)
    lit = 1 << k64  # tpyc: type(int64)
    small = uint8(1) << k64  # tpyc: type(uint8)
    # a literal left operand takes a narrow count's type too
    lk8 = 1 << k8  # tpyc: type(int8)
    lu8 = 1 << u8  # tpyc: type(uint8)
    print("free", r, s, t, v, lit, ~small, lk8, lu8)


# module-level statement: int16 << int64 into a global
gk = int64(3)
gs = int16(5) << gk  # tpyc: type(int16)


# generator body: uint64 >> int8
def gen(x: uint64, ks: list[int8]) -> Iterator[uint64]:
    for k in ks:
        yield x >> k  # tpyc: ok


# async body: int8 << uint32 after a suspension
async def async_fn(x: int8, k: uint32) -> int8:
    await asyncio.sleep(0)
    r = x << k  # tpyc: type(int8)
    return r


# comprehension: uint32 >> int64 per element
def comp(w: uint32, ks: list[int64]) -> Own[list[uint32]]:
    return [w >> k for k in ks]  # tpyc: ok


# closure: the captured int32 shifted by the nested def's int64 param
def closure(b: int32, k: int64) -> int32:
    def inner(c: int64) -> int32:
        return b << c  # tpyc: ok
    return inner(k)


# context-manager body: int64 >> uint8
def cm_body(n: int64, k: uint8) -> int64:
    with Ctx():
        r = n >> k  # tpyc: type(int64)
    return r


# try/finally: uint8 << int32
def try_finally(x: uint8, k: int32) -> uint8:
    try:
        r = x << k  # tpyc: type(uint8)
    finally:
        print("try_finally done")
    return r


# @error_return body: int32 >> uint64
@error_return(Failed)
def er_body(b: int32, k: uint64) -> int32:
    if k > 31:
        raise Failed()
    return b >> k  # tpyc: ok


# match arm: int64 << int8 and uint32 >> int64
def match_arm(sel: int32, n: int64, w: uint32, k8: int8, k64: int64) -> int64:
    match sel:
        case 0:
            return n << k8  # tpyc: ok
        case _:
            # the result keeps the left operand's uint32, whatever the count
            r = w >> k64  # tpyc: type(uint32)
            print("match_arm width", ~r)
            return r


# aug-assign: the target keeps its type whatever the count's
def aug_local(n: int64, k: int32, x: uint8, k64: int64) -> None:
    n >>= k  # tpyc: ok
    x <<= k64  # tpyc: ok
    print("aug_local", n, ~x)


# pending locals: a literal-seeded left operand keeps its own type, a
# literal-seeded count leaves the left operand's type alone, and a literal
# left operand follows a count settled wider
def pending(k64: int64, x8: int8) -> None:
    p = 1
    r = p << k64  # tpyc: type(int32)
    c = 1
    s = x8 << c  # tpyc: type(int8)
    c = k64
    lit = 1 << c  # tpyc: type(int64)
    print("pending", r, s, lit)


# a negative count raises ValueError whatever the count's type, on either shift
def negative(n: int64, k: int8) -> None:
    try:
        r = n >> k
        print("negative", r)
    except ValueError:
        print("negative ValueError")
    try:
        r = n << k
        print("negative", r)
    except ValueError:
        print("negative << ValueError")


# the unchecked wrapping shifts take any fixed-width count too, unchecked: a
# count below 0 or at or past the width is undefined, not a wrap
def wrap(y: uint32, k: int64) -> None:
    print("wrap", uint32.shl_wrap(y, k), uint32.shr_wrap(y, k))  # tpyc: ok


def main() -> None:
    k64 = int64(40)
    free_fn(1 << k64, 3, 7, 5, 0xFFFFFFFF, 3, k64 - 37, 4)
    bits = Bits(0x0F, 4)
    print("ctor", bits.word)
    print("method", bits.take(4, 8), bits.word)
    print("module", gs)
    print("gen", list(gen(2**63, [0, 1, 62])))
    print("async", asyncio.run(async_fn(3, 5)))
    print("comp", comp(0x80000000, [31, 16, 0]))
    print("closure", closure(3, 20))
    print("cm_body", cm_body(-1024, 3))
    tf = try_finally(3, 6)
    print("try_finally", tf)
    try:
        e1 = er_body(-256, 4)
        print("er_body", e1)
        e2 = er_body(1, 40)
        print("er_body", e2)
    except Failed:
        print("er_body failed")
    # bound first: a print argument that prints interleaves
    # (BUGS.md#print-arg-output-interleaves); the uint32 / uint8 spellings
    # give CPython's stubs the width `~` reads
    m0 = match_arm(0, 3, 0, 40, 0)
    m1 = match_arm(1, 0, uint32(0xF0000000), 0, 28)
    print("match_arm", m0, m1)
    aug_local(1 << k64, 8, uint8(1), 7)
    pending(3, 3)
    negative(8, -1)
    wrap(0x80000001, 4)


main()
