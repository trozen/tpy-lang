# Cancellation thrown at a resumed await should be catchable by try/except.
import asyncio


async def worker() -> None:
    await asyncio.sleep(0.5)
    print("not reached")


async def main_coro() -> None:
    task = asyncio.create_task(worker())
    await asyncio.sleep(0.001)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        print("caught")


def main() -> None:
    asyncio.run(main_coro())


main()
