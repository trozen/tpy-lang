# A bound coroutine is single-use: consuming it twice is a compile-time
# error (CPython raises RuntimeError "cannot reuse already awaited
# coroutine" at runtime).
import asyncio


async def add_one(n: int) -> int:
    return n + 1


async def main_coro() -> None:
    c = add_one(1)
    print(await c)
    print(await c)  # tpyc: error(/after it was consumed/)


def main() -> None:
    asyncio.run(main_coro())


main()
