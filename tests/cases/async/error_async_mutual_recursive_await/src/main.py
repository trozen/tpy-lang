# Mutually recursive inline `await` can't be ordered: each coro's frame stores
# the other by value (`optional<__coro_other>`), so the cycle is infinite-size.
# Rejected with a clean diagnostic instead of a confusing C++ error.
import asyncio
from tpy import Int32


async def ping(n: Int32) -> Int32:  # tpyc: error(/recursive inline .await./)
    if n <= 0:
        return 0
    return await pong(n - 1)


async def pong(n: Int32) -> Int32:
    await asyncio.sleep(0)
    return await ping(n - 1)


async def main_coro() -> None:
    print(await ping(3))


def main() -> None:
    asyncio.run(main_coro())


main()
