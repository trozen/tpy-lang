# A coroutine frame dropped mid-suspension (task never completed before
# the loop drained) runs its pending finally, like CPython's GC close.
import asyncio


async def worker() -> None:
    try:
        await asyncio.sleep(10)
    finally:
        print("worker cleanup")


async def main_coro() -> None:
    t = asyncio.create_task(worker())
    await asyncio.sleep(0)
    print("main done")


def main() -> None:
    asyncio.run(main_coro())
    print("after run")


main()
