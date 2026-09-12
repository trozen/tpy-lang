# asyncio.gather_list_settled: empty tasks list short-circuits to an empty
# result (matches CPython `gather() == []`). Pins the n==0 fast path in
# `_GatherSettledFuture.__poll__`.
import asyncio
from tpy import int32


async def main_coro() -> None:
    tasks: list[asyncio.Task[int32]] = []
    results = await asyncio.gather_list_settled(tasks)
    print("count", len(results))


def main() -> None:
    asyncio.run(main_coro())


main()
