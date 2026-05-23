# asyncio.gather_list: result order matches INPUT order regardless of
# completion order. Uses an Event to enforce a deterministic completion
# sequence rather than relying on sleep durations: `slow` (input index 0)
# parks on the event; `fast` (input index 1) sets the event before
# returning, so `fast` finishes first and `slow` second. The result list
# is [slow's 100, fast's 200] (input order) even though completion order
# was the reverse. Print interleaving confirms `fast done` lands before
# `slow done`.
import asyncio
from asyncio import Event
from tpy import Int32


async def slow(start: Event) -> Int32:
    await start
    print("slow done")
    return Int32(100)


async def fast(start: Event) -> Int32:
    start.set()
    print("fast done")
    return Int32(200)


async def main_coro() -> None:
    start = Event()
    tasks: list[asyncio.Task[Int32]] = []
    tasks.append(asyncio.create_task(slow(start)))
    tasks.append(asyncio.create_task(fast(start)))
    results = await asyncio.gather_list(tasks)
    print("result[0]:", results[0])
    print("result[1]:", results[1])


def main() -> None:
    asyncio.run(main_coro())


main()
