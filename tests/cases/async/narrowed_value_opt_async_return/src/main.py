# A narrowed value-Optional param returned from an async def derefs to
# the inner value at the coroutine return slot.
import asyncio
from tpy import Int32


async def pick(p: Int32 | None) -> Int32:
    await asyncio.sleep(0)
    if p is not None:
        return p
    return -1


async def pick_whole(p: Int32 | None) -> Int32 | None:
    await asyncio.sleep(0)
    if p is not None:
        return p
    return None


async def drive() -> None:
    print(await pick(9))
    print(await pick(None))
    w = await pick_whole(5)
    if w is not None:
        print(w)
    w2 = await pick_whole(None)
    if w2 is None:
        print("none")


asyncio.run(drive())
