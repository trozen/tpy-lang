# asyncio.Event.wait() -- CPython-shape `await event.wait()`, fast path
# (already set) and slow path (suspend, then woken by a setter task). A
# cancelled waiter raises CancelledError and frees the waiter slot, but only
# its own: a waiter parked by another task keeps it.
import asyncio
from asyncio import Event


async def setter(e: Event) -> None:
    e.set()


async def fast() -> None:
    e = Event()
    e.set()
    r = await e.wait()
    print("fast", r)


async def slow() -> None:
    e = Event()
    asyncio.create_task(setter(e))
    r = await e.wait()
    print("slow", r)


async def event_waiter(tag: str, e: Event) -> None:
    try:
        await e.wait()
        print("cancel:", tag, "woken")
    except asyncio.CancelledError:
        print("cancel:", tag, "cancelled")
        raise


async def reap(tag: str, t: asyncio.Task[None]) -> None:
    try:
        await t
        print(tag, "not cancelled (WRONG)")
    except asyncio.CancelledError:
        print(tag, "saw the cancel")


async def cancel_wait() -> None:
    e = Event()
    ta = asyncio.create_task(event_waiter("a", e))
    await asyncio.sleep(0.01)
    ta.cancel()  # tpyc: ok -- a raises at its wait and leaves the waiter slot to b
    await reap("cancel:", ta)
    tb = asyncio.create_task(event_waiter("b", e))
    await asyncio.sleep(0.01)
    e.set()
    await tb


async def plain_waiter(tag: str, e: Event) -> None:
    await e.wait()
    print(tag, "woken")


async def evict() -> None:
    e = Event()
    tb = asyncio.create_task(plain_waiter("evict: b", e))
    await asyncio.sleep(0)
    ta = asyncio.create_task(plain_waiter("evict: a", e))
    ta.cancel()  # tpyc: ok -- a's cancel at the Event leaves b's registration in place
    await reap("evict: a", ta)
    e.set()
    await tb


async def parks_after(go: Event, e: Event) -> None:
    await go.wait()
    await e.wait()
    print("woken-cancel: b woken")


async def woken_then_cancelled() -> None:
    e = Event()
    go = Event()
    ta = asyncio.create_task(plain_waiter("woken-cancel: a", e))
    tb = asyncio.create_task(parks_after(go, e))
    await asyncio.sleep(0)
    go.set()
    e.set()
    e.clear()
    # The subject: a, woken by the set, is cancelled after b parked on e.
    ta.cancel()  # tpyc: ok
    await reap("woken-cancel:", ta)
    e.set()
    await tb


async def main_coro() -> None:
    await fast()
    await slow()
    await cancel_wait()
    await evict()
    await woken_then_cancelled()


def main() -> None:
    asyncio.run(main_coro())


main()
