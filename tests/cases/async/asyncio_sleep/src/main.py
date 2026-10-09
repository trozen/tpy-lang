# asyncio.sleep suspends for its delay; sleep(0) suspends once, letting the
# other ready tasks run first (CPython's bare-yield sleep(0)), and a task
# looping on sleep(0) still lets timers fire between loop iterations. A task
# woken by a due timer runs one batch after the timer fires, as in CPython,
# unless a task wakes it first (a cancel), which queues it at that point.
import asyncio
import time
from tpy import int32

ran = False
flag = False


async def main_coro() -> None:
    print("first")
    await asyncio.sleep(0.05)
    print("second")
    await asyncio.sleep(0.05)
    print("third")


async def sets_ran() -> None:
    global ran
    ran = True


async def ticks(name: str, n: int) -> None:
    for i in range(n):
        print("ticks:", name, i)
        await asyncio.sleep(0)  # tpyc: ok -- the other ticker runs before this one resumes


async def yields() -> None:
    t = asyncio.create_task(sets_ran())
    await asyncio.sleep(0)  # tpyc: ok -- sets_ran runs here
    print("yield: spawned task ran", ran)
    await t
    a = asyncio.create_task(ticks("a", 3))
    b = asyncio.create_task(ticks("b", 3))
    await a
    await b


async def sets_flag_later() -> None:
    global flag
    await asyncio.sleep(0.02)
    flag = True


async def busy_waits() -> None:
    t = asyncio.create_task(sets_flag_later())
    n = 0
    while not flag:
        n += 1
        await asyncio.sleep(0)  # tpyc: ok -- the timer of sets_flag_later still fires
    print("busy-wait: flag seen", n > 0)
    await t


async def hop_sleeper() -> None:
    await asyncio.sleep(0.01)
    print("timer-hop: sleeper woke")


async def hop_spins(n: int32) -> None:
    for i in range(n):
        print("timer-hop: spin", i)
        await asyncio.sleep(0)


async def timer_hop() -> None:
    a = asyncio.create_task(hop_sleeper())
    await asyncio.sleep(0)
    s = asyncio.create_task(hop_spins(4))
    time.sleep(0.03)  # blocks past the sleeper's deadline
    for i in range(3):
        print("timer-hop: main", i)
        await asyncio.sleep(0)  # tpyc: ok -- the sleeper wakes one batch after its timer fires
    await a
    await s


async def staged_sleeper() -> None:
    try:
        await asyncio.sleep(0.005)
        print("staged-cancel: sleeper woke (wrong)")
    except asyncio.CancelledError:
        print("staged-cancel: sleeper cancelled")


async def staged_waiter(e: asyncio.Event) -> None:
    await e.wait()
    print("staged-cancel: event waiter woke")


async def staged_cancel() -> None:
    e = asyncio.Event()
    t = asyncio.create_task(staged_sleeper())
    w = asyncio.create_task(staged_waiter(e))
    await asyncio.sleep(0)
    time.sleep(0.02)  # blocks past the sleeper's deadline
    await asyncio.sleep(0)  # the sleeper's timer fires: its wake is staged
    print("staged-cancel: main cancels, then sets")
    t.cancel()  # tpyc: ok -- the cancel wakes the sleeper here, ahead of the waiter
    e.set()
    await asyncio.sleep(0)
    print("staged-cancel: main after")
    await t
    await w


def main() -> None:
    asyncio.run(main_coro())
    asyncio.run(yields())
    asyncio.run(busy_waits())
    asyncio.run(timer_hop())
    asyncio.run(staged_cancel())


main()
