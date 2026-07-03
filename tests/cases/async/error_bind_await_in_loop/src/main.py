# Awaiting a bound coroutine inside a loop is rejected: the handle is
# single-use and is not re-bound each iteration (mirrors the consuming-
# method loop guard).
import asyncio


async def add_one(n: int) -> int:
    return n + 1


async def main_coro() -> None:
    c = add_one(1)
    for i in range(2):
        r = await c  # tpyc: error(/Cannot await coroutine 'c' inside a loop/)
        print(r)


def main() -> None:
    asyncio.run(main_coro())


main()
