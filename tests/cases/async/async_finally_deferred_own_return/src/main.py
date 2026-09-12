# A coroutine returning an owned record from inside a `try` whose body
# SUSPENDS: the finally becomes a frame helper, so the return value is
# captured before the helper runs and moved out after it.
import asyncio
from tpy import int32, Own


class Box:
    n: int32

    def __init__(self) -> None:
        self.n = 10


async def step() -> int32:
    return 1


async def ret_after_await() -> Own[Box]:
    b = Box()
    try:
        b.n += await step()
        # The return value is captured here, but the finally runs first.
        return b
    finally:
        b.n += 1


async def driver() -> None:
    print((await ret_after_await()).n)


def main() -> None:
    asyncio.run(driver())


main()
