# asyncio.Event.wait() -- CPython-shape `await event.wait()`, fast path
# (already set) and slow path (suspend, then woken by a setter task).
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


async def main_coro() -> None:
    await fast()
    await slow()


def main() -> None:
    asyncio.run(main_coro())


main()
