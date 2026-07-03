# Passing the async def itself (missing the call) to create_task gets the
# targeted hint (sibling of the asyncio.run case, same helper).
import asyncio


async def add_one(n: int) -> int:
    return n + 1


async def main_coro() -> None:
    t = asyncio.create_task(add_one)  # tpyc: error(/not the async def itself/)
    await t


def main() -> None:
    asyncio.run(main_coro())


main()
