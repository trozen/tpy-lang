# sys.exit (and an explicit KeyboardInterrupt) inside asyncio.run ends the
# run, not just the task: it leaves asyncio.run from the main task, a spawned
# task, a gather child and a cleanup the shutdown drain runs; a main task
# suspended at the time is cancelled so its finally runs, and so are tasks
# still queued in the batch the exit left.
import asyncio
import sys
from tpy import int32


async def main_exits() -> None:
    try:
        sys.exit(6)  # tpyc: ok
    finally:
        print("main task: finally ran")


async def child() -> None:
    # The subject: a spawned task's SystemExit is not stored as its result.
    sys.exit(5)  # tpyc: ok


async def main_waits() -> None:
    t = asyncio.create_task(child())
    try:
        await asyncio.sleep(0.05)
        print("spawned task: main continued (wrong)")
    finally:
        print("spawned task: main's finally ran")


async def exiter() -> int32:
    await asyncio.sleep(0.001)
    # The subject: a gather child's exit is not the gather's failure.
    sys.exit(7)  # tpyc: ok
    return 0


async def sibling() -> int32:
    await asyncio.sleep(0.05)
    return 1


async def main_gathers() -> None:
    a = asyncio.create_task(exiter())
    b = asyncio.create_task(sibling())
    await asyncio.gather(a, b)
    print("gather: main continued (wrong)")


async def interrupter() -> None:
    # The subject: an explicitly raised KeyboardInterrupt leaves the run too.
    raise KeyboardInterrupt("x")  # tpyc: ok


async def main_interrupted() -> None:
    t = asyncio.create_task(interrupter())
    await asyncio.sleep(0.05)
    print("interrupt: main continued (wrong)")


async def stubborn() -> None:
    try:
        await asyncio.sleep(10.0)
    finally:
        # The subject: exiting from cleanup run by the shutdown drain's cancel.
        sys.exit(9)  # tpyc: ok


async def main_returns() -> None:
    t = asyncio.create_task(stubborn())
    await asyncio.sleep(0.001)


async def batch_exiter() -> None:
    await asyncio.sleep(0)
    # The subject: the exit leaves the batch while x and y are still queued in it.
    sys.exit(4)  # tpyc: ok


async def batch_spinner(tag: str) -> None:
    try:
        while True:
            await asyncio.sleep(0)
    finally:
        print("batch-exit:", tag, "finally")


async def main_batch_exit() -> None:
    e = asyncio.create_task(batch_exiter())
    x = asyncio.create_task(batch_spinner("x"))
    y = asyncio.create_task(batch_spinner("y"))
    try:
        await asyncio.sleep(0.05)
        print("batch-exit: main continued (wrong)")
    finally:
        print("batch-exit: main's finally ran")


def main() -> None:
    # Section main task: sys.exit in the main task.
    try:
        asyncio.run(main_exits())
    except SystemExit as e:
        print("main task: out of run", e.code)
    # Section spawned task: sys.exit in a spawned task while main is suspended.
    try:
        asyncio.run(main_waits())
    except SystemExit as e:
        print("spawned task: out of run", e.code)
    # Section gather: sys.exit in a child of asyncio.gather.
    try:
        asyncio.run(main_gathers())
    except SystemExit as e:
        print("gather: out of run", e.code)
    # Section interrupt: KeyboardInterrupt raised by a spawned task.
    try:
        asyncio.run(main_interrupted())
    except KeyboardInterrupt as e:
        print("interrupt: out of run", str(e))
    # Section drain: sys.exit from a finally the shutdown drain's cancel runs.
    try:
        asyncio.run(main_returns())
        print("drain: run returned (wrong)")
    except SystemExit as e:
        print("drain: out of run", e.code)
    # Section batch-exit: sys.exit leaving a batch with siblings still queued.
    try:
        asyncio.run(main_batch_exit())
    except SystemExit as e:
        print("batch-exit: out of run", e.code)


main()
