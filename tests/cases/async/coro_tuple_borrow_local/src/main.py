# A borrow-form tuple LOCAL in an async body held across an await: the coro
# frame stores it in pointer form (std::tuple<..., Box*>), so mutating through
# it after the suspension reaches the caller's object (CPython aliasing) --
# a storage-form frame field would have mutated a private copy.
import asyncio
from tpy import int32


class Box:
    val: int32
    def __init__(self, v: int32) -> None:
        self.val = v


async def bump(b: Box) -> None:
    t = (1, b)
    await asyncio.sleep(0)
    t[1].val = 99


async def driver() -> None:
    b = Box(5)
    await bump(b)
    print(b.val)


asyncio.run(driver())
