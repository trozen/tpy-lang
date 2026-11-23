# A coroutine awaited in its statement takes its generator argument by
# reference to a holder of the awaiting frame, and a fresh field-read
# receiver of its argument lives across the await.
import asyncio
from typing import Iterator
from tpy import int32


class Src:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class Holder:
    s: Src

    def __init__(self, n: int32) -> None:
        self.s = Src(n)


def gen(n: int32) -> Iterator[int32]:
    yield n
    yield n + 1


def gen_src(s: Src) -> Iterator[int32]:
    yield s.n
    yield s.n + 1


async def drain(g: Iterator[int32]) -> int32:
    await asyncio.sleep(0)
    total = 0
    for v in g:
        total += v
    return total


async def use(s: Src) -> int32:
    await asyncio.sleep(0)
    junk = [Src(999) for _ in range(10)]
    return s.n + len(junk) - 10


async def awaited() -> int32:
    total = await drain(gen(5))  # tpyc: ok
    s = Src(50)
    for i in range(2):
        total += await drain(gen_src(s))  # tpyc: ok
    return total


async def field() -> int32:
    return await use(Holder(5).s)  # tpyc: ok


def main() -> None:
    n = asyncio.run(awaited())
    print("await drain", n)
    n = asyncio.run(field())
    print("await field", n)
