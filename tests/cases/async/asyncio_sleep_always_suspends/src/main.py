# asyncio.sleep starts its delay at the first poll and always suspends once
# (sleep(0) and negative delays included), the run loop collects due timers
# between batches, and asyncio.run runs the tasks already runnable once more
# after the root completes -- all as in CPython.
import asyncio
from asyncio import Event
import time


class State:
    flag: bool

    def __init__(self) -> None:
        self.flag = False


# --- bound_sleep: a sleep created early, awaited after blocking work ---
async def bound_a() -> None:
    await asyncio.sleep(0.001)
    print("bound_sleep: a")


async def bound_b() -> None:
    s = asyncio.sleep(0.001)
    await asyncio.sleep(0)
    time.sleep(0.01)
    await s  # tpyc: ok -- the delay starts here, after a's timer has expired, so a wakes first
    print("bound_sleep: b")


async def bound_sleep() -> None:
    ta = asyncio.create_task(bound_a())
    tb = asyncio.create_task(bound_b())
    await ta
    await tb


# --- sleep0_runs_spawned: sleep(0) and sleep(-1) let a spawned task run ---
async def mark(st: State) -> None:
    st.flag = True


async def sleep0_runs_spawned() -> None:
    st = State()
    t = asyncio.create_task(mark(st))
    await asyncio.sleep(0)  # tpyc: ok -- yields once, so mark() runs here
    print("sleep0_runs_spawned: zero", st.flag)
    await t
    neg = State()
    tn = asyncio.create_task(mark(neg))
    await asyncio.sleep(-1)  # tpyc: ok -- a negative delay yields once too
    print("sleep0_runs_spawned: negative", neg.flag)
    await tn


# --- sleep0_before_expired_timer: the requeued task runs before a timer ---
async def late_timer() -> None:
    await asyncio.sleep(0.001)
    print("sleep0_before_expired_timer: timer")


async def sleep0_before_expired_timer() -> None:
    t = asyncio.create_task(late_timer())
    await asyncio.sleep(0)
    time.sleep(0.01)
    await asyncio.sleep(0)  # tpyc: ok -- requeued before the timer fires, so it resumes first
    print("sleep0_before_expired_timer: main")
    await t


# --- poll_loop: a sleep(0) polling loop does not starve a timer ---
async def ticker(st: State) -> None:
    await asyncio.sleep(0.005)
    st.flag = True


async def poll_loop() -> None:
    st = State()
    t = asyncio.create_task(ticker(st))
    while not st.flag:
        await asyncio.sleep(0)  # tpyc: ok -- each pass also fires due timers
    print("poll_loop: done")
    await t


# --- staggered: delays started together wake in delay order ---
async def nap(tag: str, d: float) -> None:
    await asyncio.sleep(d)  # tpyc: ok -- timers fire in deadline order
    print("staggered:", tag)


async def staggered() -> None:
    tc = asyncio.create_task(nap("c", 0.03))
    ta = asyncio.create_task(nap("a", 0.01))
    tb = asyncio.create_task(nap("b", 0.02))
    await tc
    await ta
    await tb


async def main_coro() -> None:
    await bound_sleep()
    await sleep0_runs_spawned()
    await sleep0_before_expired_timer()
    await poll_loop()
    await staggered()


# --- after_main: tasks runnable when the root returns still run once ---
async def waiter(ev: Event) -> None:
    try:
        await ev.wait()
        print("after_main: waiter woke")
    finally:
        print("after_main: waiter finally")


async def set_and_return(ev: Event) -> None:
    w = asyncio.create_task(waiter(ev))
    await asyncio.sleep(0.001)
    ev.set()  # tpyc: ok -- wakes the waiter, which runs before asyncio.run returns
    print("after_main: main done")


def main() -> None:
    asyncio.run(main_coro())
    ev = Event()
    asyncio.run(set_and_return(ev))
    print("after_main: after run")


main()
