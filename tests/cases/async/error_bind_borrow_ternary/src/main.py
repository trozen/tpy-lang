# A ternary over two coroutine handles is a borrowed-reference binding --
# rejected: handles are single-use and consume-only.
import asyncio


async def add_one(n: int) -> int:
    return n + 1


async def main_coro(flag: bool) -> None:
    a = add_one(1)
    b = add_one(2)
    c = a if flag else b  # tpyc: error(/cannot bind 'c' as a borrowed coroutine reference/)
    print(await c)


def main() -> None:
    asyncio.run(main_coro(True))


main()
