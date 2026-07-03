# Bound coroutine: `c = f()` binds an owned single-use handle that can be
# passed to asyncio.create_task later.
import asyncio


async def add_one(n: int) -> int:
    return n + 1


async def main_coro() -> None:
    c = add_one(41)
    t = asyncio.create_task(c)
    print(await t)


def main() -> None:
    asyncio.run(main_coro())


main()
