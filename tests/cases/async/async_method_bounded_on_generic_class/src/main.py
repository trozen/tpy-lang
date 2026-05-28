# Async method on a generic class with a BOUNDED type param. The
# out-of-class factory definition has to spell the same constraint as the
# in-class declaration (`Iterable<Int32> T`, not bare `typename T`), and
# the body's for-loop has to see the bound to pick the iteration strategy.
import asyncio
from typing import Iterable
from tpy import Int32


class Summer[T: Iterable[Int32]]:
    items: T

    def __init__(self, items: T) -> None:
        self.items = items

    async def total(self) -> Int32:
        result: Int32 = 0
        for x in self.items:
            result += x
        return result


def main() -> None:
    xs: list[Int32] = [1, 2, 3, 4, 5]
    s = Summer(xs)
    print(asyncio.run(s.total()))


main()
