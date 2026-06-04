# Awaiting a protocol-param coroutine with a collection: bind the literal to a
# local first, then await -- the form the await-arg-literal reject points users
# to. The bound local is hoisted into the resumable frame, so its deferred
# element type must resolve before the frame field is rendered.
import asyncio
from typing import Iterable
from tpy import Int32


async def consume(it: Iterable[Int32]) -> None:
    for x in it:
        await asyncio.sleep(0)
        print(x)


async def main_coro() -> None:
    data: list[Int32] = [1, 2, 3]
    await consume(data)


def main() -> None:
    asyncio.run(main_coro())


main()
