# Branch-binding DIFFERENT coroutines under one name is the same
# one-frame-type conflict as sequential rebinding -- rejected, with the
# explicit-erasure annotation as the named escape.
import asyncio


async def add_one(n: int) -> int:
    return n + 1


async def times_ten(n: int) -> int:
    return n * 10


async def main_coro(flag: bool) -> None:
    if flag:
        c = add_one(1)
    else:
        c = times_ten(2)  # tpyc: error(/cannot rebind 'c' to a different coroutine/)
    print(await c)


def main() -> None:
    asyncio.run(main_coro(True))


main()
