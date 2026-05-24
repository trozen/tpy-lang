# Task.cancel() promptly unblocks a sub-task parked on a long sleep.
# Without the `mark_runnable_now` hook on Task.cancel, the cancel
# flag would be set but the slot would stay non-runnable until the
# sleep timer naturally fires (100 seconds here) -- the test would
# hang past any reasonable runtime. With the hook, the slot is
# marked runnable immediately, the next poll observes the cancel
# at the sleep's resume cancel-check, and CancelledError unwinds in
# milliseconds.
import asyncio


async def waiter() -> None:
    try:
        await asyncio.sleep(100.0)
        print("waiter not cancelled (unexpected)")
    except asyncio.CancelledError:
        print("waiter cancelled")
        raise


async def main_coro() -> None:
    task = asyncio.create_task(waiter())
    # Let waiter park on its sleep before we cancel.
    await asyncio.sleep(0.001)
    task.cancel()
    try:
        await task
        print("main not cancelled (unexpected)")
    except asyncio.CancelledError:
        print("main caught")


def main() -> None:
    asyncio.run(main_coro())


main()
