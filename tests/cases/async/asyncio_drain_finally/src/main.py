# asyncio.run cancels and drains spawned tasks at exit so their finally
# blocks still run for fire-and-forget tasks. The background task is
# parked on a long sleep that would never naturally complete during the
# test; cancellation at run end delivers CancelledError to its next
# resume position, the wrapper try/finally runs the cleanup, and the
# task completes via the cached exception path.
import asyncio
from tpy import Int32
from asyncio import Task


async def background() -> None:
    try:
        await asyncio.sleep(60.0)
        print("not reached")
    finally:
        print("background cleanup ran")


async def main_coro() -> None:
    t: Task[None] = asyncio.create_task(background())
    del t
    print("main done")


def main() -> None:
    asyncio.run(main_coro())


main()
