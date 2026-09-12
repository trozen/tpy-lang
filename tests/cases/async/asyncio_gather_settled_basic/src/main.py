# asyncio.gather_list_settled: all-success case. Each Settled entry has
# `value` populated and `exception` is None; result order matches input.
import asyncio
from tpy import int32


async def fetch(n: int32) -> int32:
    await asyncio.sleep(0.001)
    return n * int32(2)


async def main_coro() -> None:
    tasks: list[asyncio.Task[int32]] = []
    tasks.append(asyncio.create_task(fetch(int32(1))))
    tasks.append(asyncio.create_task(fetch(int32(2))))
    tasks.append(asyncio.create_task(fetch(int32(3))))
    results = await asyncio.gather_list_settled(tasks)
    for r in results:
        if r.value is not None:
            print("ok", r.value.get())
        else:
            print("exc-slot present but expected ok")


def main() -> None:
    asyncio.run(main_coro())


main()
