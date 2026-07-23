# An async def returning a @nocopy frame local as Own[T]: the return
# moves out of the frame slot (a copy is a deleted-ctor build error).
# The awaiter owns the result and mutates it freely.
import asyncio
from tpy import Int32, nocopy, Own


@nocopy
class Box:
    v: Int32

    def __init__(self, v: Int32) -> None:
        self.v = v


async def make(v: Int32) -> Own[Box]:
    b = Box(v)
    return b


async def driver() -> Int32:
    b = await make(7)
    b.v += 1
    return b.v


def main() -> None:
    print(asyncio.run(driver()))


main()
