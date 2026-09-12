# asyncio.gather_list: outer cancel propagates the CancelledError up to
# the caller via gather's `_cancel_pending` -> cleanup-mode flip ->
# raise. With the M10 cancel-runnable-mark hook on `Task.cancel()`, the
# sub-task slots are marked runnable immediately when gather propagates
# cancel into them; both `slow()` calls observe their sleep's
# `__cancel_pending` on the next poll and exit via CancelledError
# rather than completing the sleep. `slow finished` never prints.
import asyncio
from tpy import int32, Own


async def slow() -> int32:
    try:
        # 1s never elapses -- the outer cancel lands at ~1ms; the wide
        # margin keeps the cancel-before-completion race deterministic
        # even on a heavily loaded machine.
        await asyncio.sleep(1.0)
        print("slow finished")
        return int32(0)
    except asyncio.CancelledError:
        print("slow cancelled")
        raise


async def gather_helper() -> Own[list[int32]]:
    tasks: list[asyncio.Task[int32]] = []
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
