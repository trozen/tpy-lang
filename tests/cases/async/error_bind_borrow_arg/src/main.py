# Borrowing a bound coroutine handle into a bare-protocol param is
# rejected: handles are single-use and consume-only.
import asyncio
from tpy.coro import Cancellable


async def add_one(n: int) -> int:
    return n + 1


def describe(x: Cancellable[int]) -> None:
    print("got")


async def main_coro() -> None:
    c = add_one(1)
    describe(c)  # tpyc: error(/cannot borrow a coroutine handle into argument/)
    print(await c)


def main() -> None:
    asyncio.run(main_coro())


main()
