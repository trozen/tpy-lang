# An async def relaying an Own[T] param: the return moves the param out of
# the frame (a copy is a deleted-ctor build error for @nocopy). The awaiter
# owns the result and mutates it.
import asyncio
from tpy import int32, nocopy, Own


@nocopy
class Box:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


async def relay(b: Own[Box]) -> Own[Box]:
    await asyncio.sleep(0)
    return b


async def driver() -> int32:
    b = await relay(Box(9))
    b.v += 1
    return b.v


def main() -> None:
    print(asyncio.run(driver()))


main()
