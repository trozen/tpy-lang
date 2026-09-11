# An async frame's loop var over a `readonly` reference element: the advance
# takes `&(*it)` off a const_iterator, so the frame field must be spelled
# `const Point*`. `@nocopy` makes the alternative visible -- an owning frame
# slot would copy the element and fail to compile.
import asyncio
from tpy import Int32, nocopy, readonly


@nocopy
class Point:
    x: Int32

    def __init__(self, x: Int32) -> None:
        self.x = x


async def step(n: Int32) -> Int32:
    return n


async def total(ps: readonly[list[Point]]) -> Int32:
    n = 0
    for p in ps:
        # The await splits the loop, so `p` is a frame field rather than a
        # block-scoped C++ reference.
        n += await step(p.x)
    return n


def main() -> None:
    pts: list[Point] = [Point(1), Point(2)]
    print(asyncio.run(total(pts)))


main()
