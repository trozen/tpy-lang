# Diamond dependency: both `a` and `b` inline-await the same `shared` coroutine,
# and `driver` awaits both. The topo emit must place `shared`'s struct before
# both awaiters (no edge between a and b); exercises the multi-dependent path.
import asyncio
from tpy import int32


async def a() -> int32:
    return await shared()


async def b() -> int32:
    return await shared()


async def shared() -> int32:
    await asyncio.sleep(0)
    return 1


async def driver() -> None:
    x = await a()
    y = await b()
    print(x + y)


async def main_coro() -> None:
    await driver()


def main() -> None:
    asyncio.run(main_coro())


main()
