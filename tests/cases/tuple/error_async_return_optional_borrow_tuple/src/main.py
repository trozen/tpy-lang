# A coroutine returning a nullable tuple that borrows stays refused at its
# frame, as its bare twin `-> tuple[Box, int32]` is: the borrowed element
# would be parked in the frame across a suspension, a lifetime the frame
# does not track.
import asyncio

from tpy import int32


class Box:
    def __init__(self, n: int32) -> None:
        self.n = n


async def pick(b: Box, k: int32) -> tuple[Box, int32] | None:  # tpyc: error(/res\.return_type/)
    await asyncio.sleep(0)
    if k == 0:
        return None
    return (b, k)


async def go() -> None:
    b = Box(3)
    t = await pick(b, 1)
    if t is not None:
        print(t[0].n)


def main() -> None:
    asyncio.run(go())


main()
