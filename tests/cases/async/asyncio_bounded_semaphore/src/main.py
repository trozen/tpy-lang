# asyncio.BoundedSemaphore: a Semaphore subclass whose release() rejects an
# over-release; acquire / locked / async with are inherited.
import asyncio
from asyncio import BoundedSemaphore
from tpy import Int32


class Counters:
    active: Int32
    peak: Int32

    def __init__(self) -> None:
        self.active = 0
        self.peak = 0


async def worker(sem: BoundedSemaphore, c: Counters) -> None:
    async with sem:
        c.active += 1
        if c.active > c.peak:
            c.peak = c.active
        await asyncio.sleep(0.001)
        c.active -= 1


async def main_coro() -> None:
    sem = BoundedSemaphore(2)
    print("locked0:", sem.locked())
    await sem.acquire()
    await sem.acquire()
    print("locked_full:", sem.locked())
    sem.release()
    sem.release()
    try:
        sem.release()
        print("no error")
    except ValueError:
        print("caught over-release")

    fresh = BoundedSemaphore(2)
    c = Counters()
    tasks: list[asyncio.Task[None]] = []
    i = 0
    while i < 5:
        tasks.append(asyncio.create_task(worker(fresh, c)))
        i += 1
    await asyncio.gather(*tasks)
    print("peak:", c.peak)

    # Degenerate bound: BoundedSemaphore(0).release() raises immediately.
    z = BoundedSemaphore(0)
    try:
        z.release()
        print("no zero error")
    except ValueError:
        print("caught zero over-release")

    # Negative initial value is rejected (the subclass's own __init__ guard).
    try:
        bad = BoundedSemaphore(-1)
        print("no negative error")
    except ValueError:
        print("caught negative")


def main() -> None:
    asyncio.run(main_coro())


main()
