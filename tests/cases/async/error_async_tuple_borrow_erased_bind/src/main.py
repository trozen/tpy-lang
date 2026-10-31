# A borrowing tuple coroutine bound to a handle that must be type-erased (a
# static-protocol param) is refused, naming the per-element owned spelling.
import asyncio
from typing import Iterable
from tpy import int32


class Box:
    def __init__(self, v: int32) -> None:
        self.v = v


async def pick(b: Box, xs: Iterable[int32]) -> tuple[int32, Box]:
    await asyncio.sleep(0)
    return (1, b)


async def main() -> None:
    b = Box(3)
    h = pick(b, [1, 2])  # tpyc: error(/a tuple holding a reference.*'-> tuple\[int32, Own\[Box\]\]'/)
    t = await h
    print(t[0])


asyncio.run(main())
