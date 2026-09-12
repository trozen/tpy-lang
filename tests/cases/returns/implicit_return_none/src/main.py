# A None-including return type falls through to Python's implicit
# `return None` (materialized by sema), in sync, owned-record, and
# async forms alike.
import asyncio
from tpy import int32, Own


class Point:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


def find(n: int32) -> int32 | None:
    if n > 0:
        return n * 2


def pick(n: int32) -> Own[Point | None]:
    if n > 0:
        return Point(n)


class Tag:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def choose(p: Point, t: Tag, flag: int32) -> Point | Tag | None:
    if flag == 1:
        return p
    if flag == 2:
        return t


async def afind(n: int32) -> int32 | None:
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
