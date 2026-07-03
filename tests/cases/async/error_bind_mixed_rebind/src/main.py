# A concrete coroutine binding holds exactly one frame type: rebinding
# (or branch-binding) a different coroutine under the same name is
# rejected, with the explicit-erasure annotation as the named escape.
import asyncio


async def add_one(n: int) -> int:
    return n + 1


async def times_ten(n: int) -> int:
    return n * 10


async def main_coro() -> None:
    c = add_one(1)
    c = times_ten(2)  # tpyc: error(/cannot rebind 'c' to a different coroutine/)
    print(await c)


def main() -> None:
    asyncio.run(main_coro())


main()
