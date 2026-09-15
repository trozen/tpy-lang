# `for i, j in pairs:` where `i` is already bound in an enclosing scope: the
# target ASSIGNS that slot per iteration instead of declaring a fresh one, so
# `i` holds the last element after the loop, as in CPython. Each section reads
# the reused name after the loop, which is where a fresh shadow would show.
import asyncio
from typing import Iterator
from tpy import int32


class Runner:
    base: int32

    def __init__(self, base: int32) -> None:
        self.base = base

    # method: the reused name is a plain local of the method
    def run(self, pairs: list[tuple[int32, int32]]) -> int32:
        i = 0
        for i, j in pairs:  # tpyc: ok
            self.base += j
        return self.base + i


def nested_range(pairs: list[tuple[int32, int32]]) -> int32:
    total = 0
    # the enclosing loop's variable is the reused target; the range loop has
    # its own induction, so the inner assign cannot disturb it
    for i in range(3):
        for i, j in pairs:  # tpyc: ok
            total += i + j
        total += i
    return total


def nested_container(pairs: list[tuple[int32, int32]]) -> int32:
    total = 0
    outer: list[int32] = [7, 8]
    for i in outer:
        for i, j in pairs:  # tpyc: ok
            total += i + j
        total += i
    return total


def plain_local(pairs: list[tuple[int32, int32]]) -> int32:
    i = 100
    total = i
    for i, j in pairs:  # tpyc: ok
        total += i + j
    return total + i


def gen(pairs: list[tuple[int32, int32]]) -> Iterator[int32]:
    i = 0
    # generator: the reused slot is a frame field
    for i, j in pairs:  # tpyc: ok
        yield i + j
    yield i


async def coro(pairs: list[tuple[int32, int32]]) -> int32:
    i = 0
    for i, j in pairs:  # tpyc: ok
        await asyncio.sleep(0)
    return i


def main() -> None:
    ps = [(10, 1), (20, 2)]
    print("nested_range", nested_range(ps))
    print("nested_container", nested_container(ps))
    print("plain", plain_local(ps))
    print("method", Runner(5).run(ps))
    for v in gen(ps):
        print("gen", v)
    print("async", asyncio.run(coro(ps)))


main()
