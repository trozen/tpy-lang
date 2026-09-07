# A whole-variable loop over `dict.items()` whose value is a record, across
# a suspension: the loop variable binds the borrow-form tuple.
import asyncio
from tpy import Int32


class Box:
    v: Int32

    def __init__(self, v: Int32) -> None:
        self.v = v


async def step(n: Int32) -> Int32:
    return n + 1


async def f(n: Int32) -> Int32:
    d = {n: Box(n)}
    total = 0
    for kv in d.items():
        total = await step(kv[0])
        # The element is borrowed, so this write lands in the dict.
        kv[1].v = 99
    return total + d[n].v


def main() -> None:
    print(asyncio.run(f(1)))


main()
