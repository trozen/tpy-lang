# CPython parity: create_task accepts coroutine objects only -- an existing
# Task (or any other merely-conforming awaitable) raises TypeError in
# CPython; TPy rejects at compile time.
import asyncio


async def add_one(n: int) -> int:
    return n + 1


async def main_coro() -> None:
    t = asyncio.create_task(add_one(1))
    t2 = asyncio.create_task(t)  # tpyc: error(/expects a coroutine/)
    print(await t2)


def main() -> None:
    asyncio.run(main_coro())


main()
