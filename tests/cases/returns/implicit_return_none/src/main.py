# A None-including return type falls through to Python's implicit
# `return None` (materialized by sema), in sync, owned-record, and
# async forms alike.
import asyncio
from tpy import Int32, Own


class Point:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x


def find(n: Int32) -> Int32 | None:
    if n > 0:
        return n * 2


def pick(n: Int32) -> Own[Point | None]:
    if n > 0:
        return Point(n)


class Tag:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n


def choose(p: Point, t: Tag, flag: Int32) -> Point | Tag | None:
    if flag == 1:
        return p
    if flag == 2:
        return t


async def afind(n: Int32) -> Int32 | None:
    await asyncio.sleep(0)
    if n > 0:
        return n * 3


async def main_coro() -> None:
    print(await afind(2))
    print(await afind(0))


def main() -> None:
    print(find(3))
    print(find(0))
    p = pick(0)
    print(p is None)
    pt = Point(1)
    tg = Tag(2)
    c = choose(pt, tg, 0)
    print(c is None)
    asyncio.run(main_coro())


main()
