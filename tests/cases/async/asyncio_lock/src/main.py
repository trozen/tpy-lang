# asyncio.Lock: serializes a read-modify-write across an await (count=5, not
# 1), plus acquire/release/locked, release-on-throw, and release-when-unheld.
# A cancelled acquirer never holds the lock and the next one gets it.
import asyncio
from asyncio import Lock
from tpy import int32


class Box:
    n: int32

    def __init__(self) -> None:
        self.n = 0


async def worker(lock: Lock, box: Box) -> None:
    async with lock:
        tmp = box.n
        await asyncio.sleep(0.001)
        box.n = tmp + 1


async def raise_holding(lock: Lock) -> None:
    try:
        async with lock:
            raise ValueError("boom")
    except ValueError:
        print("caught, locked:", lock.locked())


async def acquirer(tag: str, lock: Lock) -> None:
    try:
        await lock.acquire()
        print("cancel:", tag, "acquired")
        lock.release()
    except asyncio.CancelledError:
        print("cancel:", tag, "cancelled, locked", lock.locked())
        raise


async def reap(tag: str, t: asyncio.Task[None]) -> None:
    try:
        await t
        print(tag, "not cancelled (WRONG)")
    except asyncio.CancelledError:
        print(tag, "saw the cancel")


async def cancel_acquire() -> None:
    lock = Lock()
    await lock.acquire()
    ta = asyncio.create_task(acquirer("a", lock))
    tb = asyncio.create_task(acquirer("b", lock))
    await asyncio.sleep(0.01)
    ta.cancel()  # tpyc: ok -- a leaves the waiters, so the release below goes to b
    await reap("cancel:", ta)
    lock.release()
    await tb
    await lock.acquire()
    tc = asyncio.create_task(acquirer("c", lock))
    td = asyncio.create_task(acquirer("d", lock))
    await asyncio.sleep(0.01)
    lock.release()
    tc.cancel()  # tpyc: ok -- woken by the release but cancelled before it ran: d gets the lock
    await reap("cancel:", tc)
    await td
    print("cancel: locked at the end", lock.locked())


async def main_coro() -> None:
    lock = Lock()
    box = Box()
    print("locked0:", lock.locked())
    await lock.acquire()
    print("locked1:", lock.locked())
    lock.release()
    print("locked2:", lock.locked())

    try:
        lock.release()
        print("no error")
    except RuntimeError:
        print("caught release-unheld")

    # __aexit__ releases on the throw path, so the lock is free afterward.
    await raise_holding(lock)
    print("after raise:", lock.locked())

    tasks: list[asyncio.Task[None]] = []
    i = 0
    while i < 5:
        tasks.append(asyncio.create_task(worker(lock, box)))
        i += 1
    await asyncio.gather(*tasks)
    print("count:", box.n)


def main() -> None:
    asyncio.run(main_coro())
    asyncio.run(cancel_acquire())


main()
