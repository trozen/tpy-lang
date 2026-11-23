# An await in a `with` item after the first is refused: lifting it would run it
# before the earlier item is entered (BUGS.md#with-later-item-await-order).
import asyncio
from asyncio import Task
from typing import Iterator, Self
from tpy import Own, Ptr, int32, readonly


class Src:
    xs: list[int32]

    def __init__(self, n: int32) -> None:
        self.xs = [n, n + 1]


async def co(s: Src) -> int32:
    await asyncio.sleep(0)
    return s.xs[0] + s.xs[1]


class CM:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __enter__(self) -> Self:
        print("enter", self.n)
        return self

    def __exit__(self, a, b, c) -> None:
        pass


async def value(n: int32) -> int32:
    await asyncio.sleep(0)
    return n


async def body() -> None:
    with CM(await value(1)) as a, CM(await value(2)) as b:  # tpyc: error(/an `await` in a `with` item after the first/)
        print("body", a.n, b.n)


def main() -> None:
    asyncio.run(body())


main()
