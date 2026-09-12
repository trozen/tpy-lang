# asyncio.Semaphore: caps concurrency (peak=2 for Semaphore(2)); locked()
# tracks an exhausted counter; release past the initial value grows it
# (plain Semaphore, not Bounded); negative initial value is rejected.
import asyncio
from asyncio import Semaphore
from tpy import int32


class Counters:
    active: int32
    peak: int32

    def __init__(self) -> None:
        self.active = 0
        self.peak = 0


async def worker(sem: Semaphore, c: Counters) -> None:
    async with sem:
        c.active += 1
        if c.active > c.peak:
            c.peak = c.active
        await asyncio.sleep(0.001)
        c.active -= 1


async def main_coro() -> None:
    counter = Semaphore(2)
    print("locked0:", counter.locked())
    await counter.acquire()
    await counter.acquire()
    print("locked_full:", counter.locked())
    counter.release()
    counter.release()
    counter.release()
    print("locked_extra:", counter.locked())

    # Fresh Semaphore(2): the cap holds the observed peak at 2 with 5 workers.
    sem = Semaphore(2)
    c = Counters()
    tasks: list[asyncio.Task[None]] = []
    i = 0
    while i < 5:
        tasks.append(asyncio.create_task(worker(sem, c)))
        i += 1
    await asyncio.gather(*tasks)
    print("peak:", c.peak)

    try:
        bad = Semaphore(-1)
        print("no error")
    except ValueError:
        print("caught negative")


def main() -> None:
    asyncio.run(main_coro())


main()
