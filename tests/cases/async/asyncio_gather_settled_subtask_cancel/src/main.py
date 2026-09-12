# asyncio.gather_list_settled: an individual SUB-TASK cancelled independently
# (not the gather caller) has its CancelledError COLLECTED as a Settled entry,
# and the siblings still complete. This is the distinguishing return_exceptions
# =True behavior (CPython: a cancelled submitted task is treated as raised),
# and the path the `outer_cancel` test does NOT cover. The cancel is delivered
# via a `clone()` handed to a concurrent canceller -- Task.clone shares the
# TaskState, so cancelling the clone cancels the task the gather is awaiting.
import asyncio
from tpy import int32, Own


async def slow() -> int32:
    try:
        # 1s never elapses -- the canceller fires at ~2ms; the wide margin
        # keeps the cancel-before-completion race deterministic under load.
        await asyncio.sleep(1.0)
        return int32(1)
    except asyncio.CancelledError:
        # Propagate so the gather observes it and collects it.
        raise


async def fast() -> int32:
    await asyncio.sleep(0.001)
    return int32(2)


async def canceller(target: Own[asyncio.Task[int32]]) -> None:
    await asyncio.sleep(0.002)
    target.cancel()


async def gather_helper() -> Own[list[asyncio.Settled[int32]]]:
    a = asyncio.create_task(slow())
    b = asyncio.create_task(fast())
    # Hand a clone to the canceller; cancel propagates through the shared
    # TaskState to the handle the gather awaits. Append rvalue clones (not
    # the named `a`/`b`) -- appending a named @nocopy frame-local copies
    # instead of moves in async bodies (BUGS.md codegen entry); rvalue
    # clones move cleanly.
    asyncio.create_task(canceller(a.clone()))
    tasks: list[asyncio.Task[int32]] = []
    tasks.append(a.clone())
    tasks.append(b.clone())
    return await asyncio.gather_list_settled(tasks)


async def main_coro() -> None:
    results = await gather_helper()
    print("count", len(results))
    for r in results:
        if r.exception is not None:
            try:
                raise r.exception
            except asyncio.CancelledError:
                print("entry: cancelled")
            except BaseException as e:
                print("entry: unexpected", e.message)
        elif r.value is not None:
            print("entry: ok", r.value.get())


def main() -> None:
    asyncio.run(main_coro())


main()
