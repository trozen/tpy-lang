# asyncio.gather_list with an empty task list returns [] immediately on
# first poll -- no executor work, no suspension, no allocations beyond
# the empty result list.
import asyncio
from tpy import Int32


async def main_coro() -> None:
    tasks: list[asyncio.Task[Int32]] = []
    results = await asyncio.gather_list(tasks)
    print("len:", len(results))


def main() -> None:
    asyncio.run(main_coro())


main()
