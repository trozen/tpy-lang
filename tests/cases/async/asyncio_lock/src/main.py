# asyncio.Lock: serializes a read-modify-write across an await (count=5, not
# 1), plus acquire/release/locked, release-on-throw, and release-when-unheld.
import asyncio
from asyncio import Lock
from tpy import Int32


class Box:
    n: Int32

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


main()
