# readonly[tuple[Record,...]] params on async / generators keep their readonly:
# a @nocopy element + mutate-and-observe proves borrow-not-copy (async, generator,
# branch-aliased-local reads).
import asyncio
from typing import Iterator
from tpy import int32, readonly, nocopy


@nocopy
class Tag:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


async def asum(pair: readonly[tuple[Tag, Tag]]) -> int32:
    await asyncio.sleep(0.001)
    return pair[0].n + pair[1].n


def gsum(pair: readonly[tuple[Tag, Tag]]) -> Iterator[int32]:
    yield pair[0].n
    yield pair[1].n


def pick(p1: readonly[tuple[Tag, Tag]], p2: readonly[tuple[Tag, Tag]],
         c: bool) -> int32:
    t = p1 if c else p2
    return t[1].n


async def main_coro() -> None:
    a = Tag(3)
    b = Tag(4)
    print("asum:", await asum((a, b)))
    a.n = 100
    print("after:", await asum((a, b)))      # aliased: sees the mutation

    total = 0
    for v in gsum((a, b)):
        total += v
    print("gsum:", total)

    print("pick:", pick((a, b), (b, a), True))


def main() -> None:
    asyncio.run(main_coro())


main()
