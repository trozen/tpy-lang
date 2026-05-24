# Task.clone() propagates the Waker stamped at create_task time, so
# `cancel()` on the clone wakes the same slot as `cancel()` on the
# original. Pins the M10 invariant load-bearing for gather_list's
# cancel propagation: gather Rc-clones each user Task into its own
# list and calls cancel() on the clone -- if the clone's Waker were
# null, the runnable-mark would never fire and gather would hang on
# any sub-task with no natural wake.
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
    dup = task.clone()
    # Park waiter on its sleep before we cancel via the clone.
    await asyncio.sleep(0.001)
    dup.cancel()
    try:
        await task
        print("main not cancelled (unexpected)")
    except asyncio.CancelledError:
        print("main caught")


def main() -> None:
    asyncio.run(main_coro())


main()
