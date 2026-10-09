# Timeout path: inner coroutine still pending when the deadline fires.
# `wait_for` must cancel the inner, pump it to completion, and raise
# `TimeoutError` (a builtin, propagated through the @native
# `tpy::TimeoutError` exception type). The inner can be parked on a
# Queue.get or a Lock.acquire, which stays usable after the timeout. The
# timed-out task resumes one batch after the deadline timer fires.
import asyncio
import time
from asyncio import Lock, Queue
from tpy import int32


async def slow() -> int:
    await asyncio.sleep(1.0)
    return 42


async def main_coro() -> None:
    try:
        v = await asyncio.wait_for(slow(), 0.01)
        print("not reached")
        print(v)
    except TimeoutError:
        print("timed out")


async def primitives() -> None:
    q: Queue[int32] = Queue(0)
    try:
        await asyncio.wait_for(q.get(), 0.01)  # tpyc: ok -- the parked get takes the cancel
        print("queue: got (WRONG)")
    except TimeoutError:
        print("queue: get timed out")
    q.put_nowait(5)
    print("queue: get after", await q.get())
    lock = Lock()
    await lock.acquire()
    try:
        await asyncio.wait_for(lock.acquire(), 0.01)  # tpyc: ok -- the parked acquire takes the cancel
        print("lock: acquired (WRONG)")
    except TimeoutError:
        print("lock: acquire timed out, locked", lock.locked())
    lock.release()
    await lock.acquire()
    print("lock: acquired after")


async def hop_getter(q: Queue[int32]) -> None:
    try:
        await asyncio.wait_for(q.get(), 0.01)
        print("timeout-hop: got (WRONG)")
    except TimeoutError:
        print("timeout-hop: getter timed out")


async def timeout_hop() -> None:
    q: Queue[int32] = Queue()
    a = asyncio.create_task(hop_getter(q))
    await asyncio.sleep(0)
    time.sleep(0.03)  # blocks past the getter's deadline
    for i in range(5):
        print("timeout-hop: main", i)
        await asyncio.sleep(0)  # tpyc: ok -- the getter resumes one batch after the timer fires
    await a


def main() -> None:
    asyncio.run(main_coro())
    asyncio.run(primitives())
    asyncio.run(timeout_hop())


main()
