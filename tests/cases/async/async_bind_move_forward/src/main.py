# Ownership-transfer forms of a bound coroutine: binding by name at the
# source's last use moves the handle; an Own[Cancellable[T]] param both
# receives a bound handle and awaits it directly.
import asyncio
from tpy import Own
from tpy.coro import Cancellable


async def add_one(n: int) -> int:
    return n + 1


async def consume(c: Own[Cancellable[int]]) -> int:
    return await c


async def main_coro() -> None:
    c = add_one(1)
    d = c
    print(await d)
    e = add_one(9)
    print(await consume(e))


def main() -> None:
    asyncio.run(main_coro())


main()
