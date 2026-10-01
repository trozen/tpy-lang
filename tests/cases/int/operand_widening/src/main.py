# A binary operator over two fixed-width ints of different types converts the
# operand that widens into the other's type (same sign to more bits, unsigned
# into a strictly wider signed type) and computes in the wider type, at every
# position; an aug-assign into a declared wider slot takes the same operator.
from __future__ import annotations
from typing import Iterator
from tpy import int8, int32, int64, uint8, uint32, Own, error_return, ReturnException
import asyncio


class Failed(Exception, ReturnException):
    pass


class Ctx:
    def __enter__(self) -> Ctx:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


class Acc:
    total: int64

    # constructor: int64 // int32
    def __init__(self, a: int64, b: int32) -> None:
        q = a // b  # tpyc: type(int64)
        self.total = q

    # method: int64 * int32, and an aug-assign into an int64 field
    def add(self, a: int64, b: int32) -> int64:
        m = a * b  # tpyc: type(int64)
        self.total += b  # tpyc: ok
        return m


# free function: int64 + int32 either way round, and int64 - int32
def free_fn(a: int64, b: int32) -> None:
    s = a + b  # tpyc: type(int64)
    r = b + a  # tpyc: type(int64)
    d = a - b  # tpyc: type(int64)
    print("free", s, r, d)


# module-level statement: int64 % int32 into a global
ga = int64(2**40)
gb = int32(2147483647)
gm = ga % gb  # tpyc: type(int64)


# generator body: int64 & int32 and int64 ** int32
def gen(a: int64, b: int32, e: int32) -> Iterator[int64]:
    yield a & b  # tpyc: ok
    yield a ** e  # tpyc: ok


# async body: int64 + int32 after a suspension
async def async_fn(a: int64, b: int32) -> int64:
    await asyncio.sleep(0)
    r = a + b  # tpyc: type(int64)
    return r


# comprehension: int64 * int32 per element
def comp(a: int64, bs: list[int32]) -> Own[list[int64]]:
    return [a * b for b in bs]  # tpyc: ok


# closure: a nested def reads the captured int64 against its int32 param
def closure(a: int64, b: int32) -> int64:
    def inner(c: int32) -> int64:
        return a - c  # tpyc: ok
    return inner(b)


# context-manager body: int64 // int32
def cm_body(a: int64, b: int32) -> int64:
    with Ctx():
        r = a // b  # tpyc: type(int64)
    return r


# try/finally: int64 % int32
def try_finally(a: int64, b: int32) -> int64:
    try:
        r = a % b  # tpyc: type(int64)
    finally:
        print("try_finally done")
    return r


# @error_return body: int64 // int32
@error_return(Failed)
def er_body(a: int64, b: int32) -> int64:
    if b == 0:
        raise Failed()
    return a // b  # tpyc: ok


# match arm: int64 & int32, int64 ** int32
def match_arm(k: int32, a: int64, b: int32) -> int64:
    match k:
        case 0:
            return a & b  # tpyc: ok
        case _:
            return a ** b  # tpyc: ok


# mixed-sign pairs and a narrower signed one
def mixed(u8: uint8, u32: uint32, i8: int8, b: int32, a: int64) -> None:
    x = u8 + b  # tpyc: type(int32)
    y = u32 + a  # tpyc: type(int64)
    z = i8 + b  # tpyc: type(int32)
    print("mixed", x, y, z)


# aug-assign into a declared int64 local
def aug_local(a: int64, b: int32) -> int64:
    x: int64 = a
    x += b  # tpyc: ok
    x -= b // 2  # tpyc: ok
    return x


def main() -> None:
    a = int64(2**40)
    b = 2147483647
    free_fn(a, b)
    acc = Acc(a, b)
    print("ctor", acc.total)
    print("method", acc.add(3000000000, 3), acc.total)
    print("module", gm)
    print("gen", list(gen(3, b, 30)))
    print("async", asyncio.run(async_fn(a, b)))
    print("comp", comp(3000000000, [3, -3, 2]))
    print("closure", closure(a, b))
    print("cm_body", cm_body(a, b))
    t = try_finally(a, b)
    print("try_finally", t)
    try:
        print("er_body", er_body(a, b))
    except Failed:
        print("er_body failed")
    print("match_arm", match_arm(0, a + 5, b), match_arm(1, 3, 30))
    mixed(200, 4000000000, -100, b - 1000, a)
    print("aug_local", aug_local(a, b))


main()
