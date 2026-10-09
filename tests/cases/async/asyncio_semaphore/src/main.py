# asyncio.Semaphore: caps concurrency (peak=2 for Semaphore(2)); locked()
# tracks an exhausted counter; release past the initial value grows it
# (plain Semaphore, not Bounded); negative initial value is rejected. A
# cancelled acquirer leaves the waiters and takes no permit; one woken by a
# release but cancelled before it ran hands the permit to the next waiter.
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


async def sem_acquirer(section: str, tag: str, sem: Semaphore) -> None:
    try:
        await sem.acquire()
        print(section, tag, "acquired")
        sem.release()
    except asyncio.CancelledError:
        print(section, tag, "cancelled")
        raise


async def reap(tag: str, t: asyncio.Task[None]) -> None:
    try:
        await t
        print(tag, "not cancelled (WRONG)")
    except asyncio.CancelledError:
        print(tag, "saw the cancel")


async def cancel_acquire() -> None:
    sem = Semaphore(1)
    await sem.acquire()
    ta = asyncio.create_task(sem_acquirer("cancel:", "a", sem))
    tb = asyncio.create_task(sem_acquirer("cancel:", "b", sem))
    await asyncio.sleep(0.01)
    ta.cancel()  # tpyc: ok -- a leaves the waiters, so the release below goes to b
    await reap("cancel:", ta)
    print("cancel: locked with one waiter left", sem.locked())
    sem.release()
    await tb
    print("cancel: locked at the end", sem.locked())


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


async def handoff() -> None:
    sem = Semaphore(0)
    ta = asyncio.create_task(sem_acquirer("handoff:", "a", sem))
    tb = asyncio.create_task(sem_acquirer("handoff:", "b", sem))
    await asyncio.sleep(0.01)
    sem.release()
    ta.cancel()  # tpyc: ok -- a was woken by the release; the permit goes to b
    await reap("handoff:", ta)
    await tb
    print("handoff: locked at the end", sem.locked())


def main() -> None:
    asyncio.run(main_coro())
    asyncio.run(cancel_acquire())
    asyncio.run(handoff())


main()
