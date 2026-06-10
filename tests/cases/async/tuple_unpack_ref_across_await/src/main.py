# Reference-type tuple-unpack targets held across an await: a, b must survive
# as `T*` aliases of the live list elements (not stale copies), so mutating
# them after the suspension is visible through the source list.
import asyncio


class Box:
    n: int

    def __init__(self, n: int) -> None:
        self.n = n


def first_two(items: list[Box]) -> tuple[Box, Box]:
    return (items[0], items[1])


async def bump(items: list[Box]) -> int:
    a, b = first_two(items)
    await asyncio.sleep(0)
    a.n += 10
    b.n += 20
    return a.n + b.n


async def amain() -> None:
    items = [Box(1), Box(2)]
    print(await bump(items))
    print(items[0].n, items[1].n)


asyncio.run(amain())
