# An annotation-only declaration (`t: tuple[int32, int32]`) default-constructs
# the slot the later assignment writes, for every VALUE family: a value tuple,
# a value-repr `int32 | None`, a str, an enum and a ValueType record. The
# default-construct is the right render because each of these has one -- the
# name is unreadable until the assignment, so the slot's initial contents are
# unobservable -- and it is the same predecl a branch-first hoist emits for the
# same families.
import asyncio
from enum import Enum
from tpy import int32, ValueType
from typing import Iterator


class Color(Enum):
    RED = 1
    BLUE = 2


class Pt(ValueType):
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class Reader:
    seed: int32

    def __init__(self, seed: int32) -> None:
        self.seed = seed

    # method position
    def read(self) -> int32:
        t: tuple[int32, int32]  # tpyc: ok
        t = (self.seed, 2)
        return t[0]


# free function: every admitted value family
def free_position(c: bool) -> int32:
    t: tuple[int32, int32]  # tpyc: ok
    t = (1, 2)
    n: int32 | None  # tpyc: ok
    n = 4 if c else None
    s: str  # tpyc: ok
    s = "abc"
    col: Color  # tpyc: ok
    col = Color.BLUE
    p: Pt  # tpyc: ok
    p = Pt(5)
    total = t[0] + len(s) + p.x
    if col == Color.BLUE:
        total += 100
    if n is not None:
        total += n
    return total


# generator: the slot sits in the resumable body
def gen() -> Iterator[int32]:
    t: tuple[int32, int32]  # tpyc: ok
    t = (7, 8)
    yield t[0]
    yield t[1]


# async: the coroutine sibling
async def coro() -> int32:
    n: int32 | None  # tpyc: ok
    n = 3
    await asyncio.sleep(0)
    if n is None:
        return 0
    return n


def main() -> None:
    print("free:", free_position(True), free_position(False))
    print("method:", Reader(9).read())
    print("gen:", end=" ")
    for v in gen():
        print(v, end=" ")
    print()
    print("async:", asyncio.run(coro()))


main()
