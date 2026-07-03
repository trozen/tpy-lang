# A bound coroutine held across an await suspension lives in the coro frame
# as a concrete optional<__coro_*> field and is still consumable after
# resume.
import asyncio


async def add_one(n: int) -> int:
    return n + 1


async def main_coro() -> None:
    c = add_one(43)
    await asyncio.sleep(0.001)
    t = asyncio.create_task(c)
    print(await t)


def main() -> None:
    asyncio.run(main_coro())


main()
