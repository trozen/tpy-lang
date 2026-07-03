# Borrowing a bound coroutine handle into a protocol-annotated local is
# rejected: handles are single-use and consume-only.
import asyncio
from tpy.coro import Cancellable


async def add_one(n: int) -> int:
    return n + 1


async def main_coro() -> None:
    c = add_one(1)
    x: Cancellable[int] = c  # tpyc: error(/cannot borrow a coroutine handle into variable/)
    print(await x)


def main() -> None:
    asyncio.run(main_coro())


main()
