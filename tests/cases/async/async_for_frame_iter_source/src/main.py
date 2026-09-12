# The async sibling: a sync `for` with an `await` in its body shares the
# iter_next for-strategy with generators, so an async coro embeds the source's
# frame-emitted __iter__ struct the same way. Async units are seeded BEFORE
# generator methods, so this one is mis-ordered whatever the declaration order.
import asyncio
from tpy import int32, Own
from typing import Iterator


class Point:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class Bag:
    items: list[Point]

    def __init__(self, items: Own[list[Point]]) -> None:
        self.items = items

    def __iter__(self) -> Iterator[Point]:
        for p in self.items:
            yield p
            yield p


# Mutating through the loop element across a suspension: the element must alias
# the bag's storage, and the change is read back off the bag afterwards.
async def bump(bag: Bag) -> int32:
    total = 0
    for p in bag:
        await asyncio.sleep(0)
        p.x += 10
        total += p.x
    return total


async def amain() -> None:
    bag = Bag([Point(1), Point(2)])
    print(await bump(bag))
    print("mutations reached the bag:", bag.items[0].x, bag.items[1].x)


asyncio.run(amain())
