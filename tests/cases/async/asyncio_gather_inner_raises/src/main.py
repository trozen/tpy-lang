# asyncio.gather_list: one sub-task raises mid-flight. gather_list
# propagates `cancel()` to the still-pending siblings, waits for them
# to settle, then re-raises the first exception observed.
#
# Deterministic structure (no timing race):
#   - `bad` (input idx 0) raises ValueError on its FIRST poll -- no
#     suspension, just `print + raise`. The executor pops bad before
#     good (FIFO spawn order), so bad runs to its raise before any
#     other progress.
#   - `good` (input idx 1) suspends on a short sleep. The sleep
#     duration doesn't gate ordering -- bad has already finished by
#     the time good is polled. The sleep exists purely as a
#     suspension hook so that when gather_list propagates `cancel()`
#     into good, the resume cancel-check observes the flag on the
#     next executor poll (sleep wake) and good exits via
#     CancelledError instead of completing normally. "good finished"
#     must NOT print.
import asyncio
from tpy import Int32


async def good() -> Int32:
    await asyncio.sleep(0.001)
    print("good finished")
    return Int32(99)


async def bad() -> Int32:
    print("bad raising")
    raise ValueError("bad")


async def main_coro() -> None:
    tasks: list[asyncio.Task[Int32]] = []
    tasks.append(asyncio.create_task(bad()))
    tasks.append(asyncio.create_task(good()))
    try:
        results = await asyncio.gather_list(tasks)
        print("got", len(results))
    except ValueError as e:
        print("caught ValueError:", e)


def main() -> None:
    asyncio.run(main_coro())


main()
