# A named Task local appended to a list inside an async body is moved out of
# its frame slot at its last use (Task is @nocopy, so a copy would not build).
import asyncio
from tpy import Int32


async def fetch(n: Int32) -> Int32:
    await asyncio.sleep(0.001)
    return n * 2


async def main_coro() -> None:
    tasks: list[asyncio.Task[Int32]] = []
    a = asyncio.create_task(fetch(1))
    tasks.append(a)  # tpyc: ok
    b = asyncio.create_task(fetch(2))
    tasks.append(b)  # tpyc: ok
    results = await asyncio.gather(*tasks)
    for r in results:
        print(r)


def main() -> None:
    asyncio.run(main_coro())


main()
