# `await t` where `t` is a for-loop variable over `list[Task[T]]`. The
# loop element is already a pointer alias into the list, so the await
# borrow must not re-take its address.
import asyncio
from tpy import int32
from asyncio import Task


async def work(n: int32) -> int32:
    return n


async def main_coro() -> None:
    tasks: list[Task[int32]] = []
    tasks.append(asyncio.create_task(work(1)))
    tasks.append(asyncio.create_task(work(2)))
    tasks.append(asyncio.create_task(work(3)))
    total = 0
    for t in tasks:
        total += await t
    print(total)


def main() -> None:
    asyncio.run(main_coro())


main()
