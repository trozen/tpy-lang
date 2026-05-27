# asyncio.gather_list_settled: exceptions at the boundary indices (first
# and last), success in the middle. Exercises the input-order reassembly
# when the exc-side scan must run for index 0 and the final index (the
# `mixed` case only has an exception at index 1). Pins that the parallel
# arrival arrays reassemble by input index regardless of where the
# exceptions land.
import asyncio
from tpy import Int32


async def good(n: Int32) -> Int32:
    await asyncio.sleep(0.001)
    return n


async def bad(tag: Int32) -> Int32:
    await asyncio.sleep(0.001)
    raise ValueError(f"boom-{tag}")


async def main_coro() -> None:
    tasks: list[asyncio.Task[Int32]] = []
    tasks.append(asyncio.create_task(bad(Int32(0))))     # exc at index 0
    tasks.append(asyncio.create_task(good(Int32(99))))   # ok at index 1
    tasks.append(asyncio.create_task(bad(Int32(2))))     # exc at last index
    results = await asyncio.gather_list_settled(tasks)
    for r in results:
        if r.exception is not None:
            try:
                raise r.exception
            except BaseException as e:
                print("exc:", e.message)
        elif r.value is not None:
            print("ok:", r.value.get())


def main() -> None:
    asyncio.run(main_coro())


main()
