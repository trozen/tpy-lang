# Yielding a PARAM at a container / record yield slot, and an annotation-only
# local decl in a resumable body: the frame captures such a param as a
# reference member, so the bare name read is already the borrow the slot wants.
import asyncio
from typing import Iterator
from tpy import Int32


class P:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x


def each(xs: list[Int32]) -> Iterator[list[Int32]]:
    yield xs  # the list param handed out by reference, twice
    yield xs


def rep(b: P) -> Iterator[P]:
    yield b  # the record param handed out by reference
    yield b


def pairs(d: dict[str, Int32]) -> Iterator[dict[str, Int32]]:
    yield d
    yield d


async def step(n: Int32) -> Int32:
    return n + 1


async def late() -> Int32:
    x: Int32  # annotation only: the frame already declares the field
    s: str  # same for a str slot -- no default-construct line either
    await step(0)
    x = 1
    s = "hi"
    return x + len(s)


def late_gen() -> Iterator[Int32]:
    n: Int32  # the generator flavor of the same annotation-only decl
    yield 0
    n = 5
    yield n


async def amain() -> None:
    print(await late())


def main() -> None:
    xs = [1, 2]
    for got in each(xs):
        # A mutation through the yielded borrow is visible on the caller's
        # list -- a copy would hide the second round's growth.
        got.append(9)
        print(len(xs), xs[len(xs) - 1])

    p = P(1)
    for gotp in rep(p):
        gotp.x += 10
        print(p.x)

    d = {"a": 1}
    for gotd in pairs(d):
        gotd["b"] = 2
        print(len(d))

    for got_n in late_gen():
        print(got_n)

    asyncio.run(amain())


main()
