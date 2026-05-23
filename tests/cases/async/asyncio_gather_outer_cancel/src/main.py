# asyncio.gather_list: outer cancel propagates the CancelledError up to
# the caller via gather's `_cancel_pending` -> cleanup-mode flip ->
# raise. v1.5 limitation: because `task.cancel()` doesn't currently
# mark the slot runnable, gather is only polled when a sub-task
# naturally wakes -- so the sub-tasks here complete normally before
# gather has a chance to propagate cancel into them. The test pins
# the CancelledError surfacing at the main_coro boundary; the inner-
# observation case is covered by `asyncio_gather_inner_raises` (where
# the sub-task wake order makes cancel inside a still-pending sibling
# reachable).
import asyncio
from tpy import Int32


async def slow() -> Int32:
    await asyncio.sleep(0.005)
    print("slow finished")
    return Int32(0)


async def gather_helper() -> list[Int32]:
    tasks: list[asyncio.Task[Int32]] = []
    tasks.append(asyncio.create_task(slow()))
    tasks.append(asyncio.create_task(slow()))
    return await asyncio.gather_list(tasks)


async def main_coro() -> None:
    gtask = asyncio.create_task(gather_helper())
    await asyncio.sleep(0.001)
    gtask.cancel()
    try:
        results = await gtask
        print("got", len(results))
    except asyncio.CancelledError:
        print("gather cancelled")


def main() -> None:
    asyncio.run(main_coro())


main()
