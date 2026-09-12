# Both field-type rewrites exercised together inside an async method body: a
# templated method (protocol param `extra`, so its coro struct orders correctly
# vs the awaited free coro) that awaits a hoisted-local iterable (`local`, a
# frame_slot -> deref rewrite) and then a param. Guards that the body-context
# spelling applies inside a method, not just a free function.
import asyncio
from typing import Iterable
from tpy import int32


async def consume(it: Iterable[int32]) -> None:
    for x in it:
        await asyncio.sleep(0)
        print(x)


class Runner:
    async def go(self, extra: Iterable[int32]) -> None:
        local: list[int32] = [7, 8]
        await consume(local)
        await consume(extra)


async def main_coro() -> None:
    r = Runner()
    data: list[int32] = [9]
    await r.go(data)


def main() -> None:
    asyncio.run(main_coro())


main()
