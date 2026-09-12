# A spawned task that itself calls asyncio.create_task forces the
# executor's slots vector to grow while inside Executor::poll_slot.
# Regression guard for the use-after-free where poll_slot held an
# `auto& slot = slots[id]` reference across the recursive poll_any
# call -- the reference would dangle after slots reallocated.
import asyncio
from tpy import int32
from asyncio import Task


async def grandchild() -> int32:
    await asyncio.sleep(0.001)
    return int32(7)


async def child() -> int32:
    g: Task[int32] = asyncio.create_task(grandchild())
    return await g + int32(1)


async def main_coro() -> None:
    c: Task[int32] = asyncio.create_task(child())
    print(await c)


def main() -> None:
    asyncio.run(main_coro())


main()
