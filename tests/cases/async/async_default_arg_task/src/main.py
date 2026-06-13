# An async def with a default arg, called via create_task (the factory path),
# applies the omitted default. (Inline `await add(5)` is a separate filed gap.)
import asyncio
from tpy import Int32


async def add(a: Int32, b: Int32 = 10) -> Int32:
    await asyncio.sleep(0)
    return a + b


async def with_default() -> Int32:
    t = asyncio.create_task(add(5))       # default b=10 -> 15
    return await t


async def with_override() -> Int32:
    t = asyncio.create_task(add(5, 2))    # override -> 7
    return await t


def main() -> None:
    print(asyncio.run(with_default()))
    print(asyncio.run(with_override()))


main()
