# asyncio.gather_list degenerate N=1 case: same machinery as multi-task but
# the inner loop runs once. Pin to catch off-by-ones in the completion-
# order reconstruction or the cleanup propagation.
import asyncio
from tpy import int32


async def producer() -> int32:
    await asyncio.sleep(0.001)
    return int32(42)


async def main_coro() -> None:
    tasks: list[asyncio.Task[int32]] = []
    tasks.append(asyncio.create_task(producer()))
    results = await asyncio.gather_list(tasks)
    print(results[0])


def main() -> None:
    asyncio.run(main_coro())


main()
