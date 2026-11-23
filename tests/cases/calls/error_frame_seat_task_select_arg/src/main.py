# A select handed to a coroutine the statement does not await (a task's) is
# refused, as on master: with a named arm here, and the same with fresh arms
# (`create_task(consume([k] * 0 or [k, k]))`).
import asyncio
from typing import Iterable
from tpy import int32


async def consume(it: Iterable[int32]) -> int32:
    t = 0
    for x in it:
        await asyncio.sleep(0)
        t += x
    return t


async def task_named(xs: list[int32], k: int32) -> int32:
    t = asyncio.create_task(consume(xs or [k]))  # tpyc: error(/not yet supported/)
    xs.append(100)
    return await t


def main() -> None:
    print("task named", asyncio.run(task_named([], 3)),
          asyncio.run(task_named([1, 2], 3)))


main()
