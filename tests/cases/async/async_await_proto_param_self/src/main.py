# Awaiting a protocol-param coroutine with a `self.field` arg from an async
# method. The sub-future field type must spell `self` as the captured frame
# field `__self` (matching the emplace), not the user-level `self`. Uses a
# method with its own protocol param (a templated coro struct) so the awaited
# free coro orders correctly -- the concrete-method form is blocked by a
# separate, pre-existing struct-ordering bug (see BUGS.md).
import asyncio
from typing import Iterable
from tpy import int32, Own


async def consume(it: Iterable[int32]) -> None:
    for x in it:
        await asyncio.sleep(0)
        print(x)


class Holder:
    rows: list[int32]

    def __init__(self, rows: Own[list[int32]]) -> None:
        self.rows = rows

    async def run(self, extra: Iterable[int32]) -> None:
        await consume(self.rows)
        await consume(extra)


async def main_coro() -> None:
    h = Holder([1, 2])
    data: list[int32] = [3, 4]
    await h.run(data)


def main() -> None:
    asyncio.run(main_coro())


main()
