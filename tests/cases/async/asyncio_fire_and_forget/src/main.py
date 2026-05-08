# Fire-and-forget: a task spawned via create_task and never awaited
# still runs to completion as long as the main coroutine yields long
# enough for the executor to drive it. The user's Task handle is
# dropped (`del t`) before the task completes; the executor's
# spawned-list wrapper keeps the TaskState alive until the frame is
# Ready.
#
# Synchronization uses a Future (set by background, awaited by main)
# rather than a timer race -- avoids depending on OS scheduling for
# `background_sleep < main_sleep` ordering.
import asyncio
from asyncio import Future
from tpy import Int32
from tpy.coro import Task


async def background(done: Future[Int32]) -> None:
    print("background ran")
    done.set_result(Int32(0))  # sentinel; only the wake matters


async def main_coro() -> None:
    done: Future[Int32] = Future[Int32]()
    t: Task[None] = asyncio.create_task(background(done))
    # Drop the handle without awaiting; the executor still drives the task.
    del t
    _ = await done
    print("main done")


def main() -> None:
    asyncio.run(main_coro())


main()
