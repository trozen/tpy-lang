# Concurrent task execution: two tasks created with create_task should run
# concurrently with the main coroutine across asyncio.sleep boundaries.
# Both tasks' "pre" prints land before either "post" because the executor
# drives spawned tasks once while the main coro is parked on its first
# await; both sleeps run in parallel.
import asyncio
from tpy import Int32
from asyncio import Task


async def doubler(n: Int32, label: str) -> Int32:
    print(label, "pre")
    await asyncio.sleep(0.001)
    print(label, "post")
    return n * Int32(2)


async def main_coro() -> None:
    t1: Task[Int32] = asyncio.create_task(doubler(Int32(5), "t1"))
    t2: Task[Int32] = asyncio.create_task(doubler(Int32(7), "t2"))
    a = await t1
    b = await t2
    print(a + b)


def main() -> None:
    asyncio.run(main_coro())


main()
