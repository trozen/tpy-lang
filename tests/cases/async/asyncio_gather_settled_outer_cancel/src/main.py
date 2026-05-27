# asyncio.gather_list_settled: cancelling the gather CALLER (the task awaiting
# the gather) propagates `CancelledError` UP to the caller -- it is NOT swallowed
# into the result list. The gather first propagates cancel into its unsettled
# sub-tasks (cleanup), then the CancelledError unwinds out through the awaiting
# frame. Matches CPython: cancelling gather() cancels it. (The collect-as-Settled
# behavior is for an *independently-cancelled sub-task* -- see
# asyncio_gather_settled_subtask_cancel.)
import asyncio
from tpy import Int32


async def slow() -> Int32:
    await asyncio.sleep(0.005)
    return Int32(0)


async def gather_helper() -> list[asyncio.Settled[Int32]]:
    tasks: list[asyncio.Task[Int32]] = []
    tasks.append(asyncio.create_task(slow()))
    tasks.append(asyncio.create_task(slow()))
    return await asyncio.gather_list_settled(tasks)


async def main_coro() -> None:
    gtask = asyncio.create_task(gather_helper())
    await asyncio.sleep(0.001)
    gtask.cancel()
    # Outer cancel of `gather_helper` propagates upward (matching CPython:
    # cancellation of the gather caller is NOT absorbed by `return_exceptions
    # =True` -- only sub-task cancellations are). The cancel additionally
    # tears through the gather into the sub-tasks so any cleanup runs.
    try:
        results = await gtask
        print("got", len(results), "results (unexpected, cancel should propagate)")
    except asyncio.CancelledError:
        print("outer cancelled")


def main() -> None:
    asyncio.run(main_coro())


main()
