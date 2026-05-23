# asyncio.gather_list: three tasks complete concurrently, results returned in
# input order. Each task does a short sleep + doubles its argument; the
# gathered list mirrors the input order regardless of which task finishes
# the sleep first.
import asyncio
from tpy import Int32


async def fetch(n: Int32) -> Int32:
    await asyncio.sleep(0.001)
    return n * Int32(2)


async def main_coro() -> None:
    tasks: list[asyncio.Task[Int32]] = []
    tasks.append(asyncio.create_task(fetch(Int32(1))))
    tasks.append(asyncio.create_task(fetch(Int32(2))))
    tasks.append(asyncio.create_task(fetch(Int32(3))))
    results = await asyncio.gather_list(tasks)
    for r in results:
        print(r)


def main() -> None:
    asyncio.run(main_coro())


main()
