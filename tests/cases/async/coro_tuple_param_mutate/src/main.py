# A reference-member tuple PARAM mutated through after an await: the coro
# frame captures the borrow tuple in pointer form, so the mutation reaches
# the caller's object across the suspension (CPython aliasing).
import asyncio
from tpy import Int32


class Box:
    val: Int32
    def __init__(self, v: Int32) -> None:
        self.val = v


async def bump(p: tuple[Int32, Box]) -> None:
    await asyncio.sleep(0)
    p[1].val = 99


async def driver() -> None:
    b = Box(5)
    await bump((1, b))
    print(b.val)


asyncio.run(driver())
