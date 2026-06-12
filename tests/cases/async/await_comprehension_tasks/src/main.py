# A comprehension building Task[T] handles (Own, @nocopy) resolves to list and
# the Own element collapses to storage form, so the await-loop compiles.
import asyncio
from tpy import Int32


async def work(n: Int32) -> Int32:
    return n


async def main_coro() -> None:
    tasks = [asyncio.create_task(work(i)) for i in range(4)]  # tpyc: type(/list\[Task/)
    total = 0
    for t in tasks:
        total += await t
    print(total)


def main() -> None:
    asyncio.run(main_coro())


main()
