# A collection literal at a protocol parameter of a plain function the
# statement awaits has no frame to hold it across the suspension: refused.
# A named iterable there crashes instead (BUGS.md#awaited-plain-protocol-arg-no-slot).
import asyncio
from typing import Iterable
from tpy import Own
from asyncio import Task


async def total(xs: Iterable[int]) -> None:
    await asyncio.sleep(0)
    t = 0
    for x in xs:
        t += x
    print("total", t)


def start(xs: Iterable[int]) -> Own[Task[None]]:
    return asyncio.create_task(total(xs))


async def main() -> None:
    k = 4
    await start([k, k, k])  # tpyc: error(/does not yet support a collection literal argument/)


asyncio.run(main())
