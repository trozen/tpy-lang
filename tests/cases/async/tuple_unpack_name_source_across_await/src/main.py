# Tuple-unpack from a stable NAME source (`a, b = t`, not a direct call) held
# across an await: t is a frame-resident borrow tuple, so a, b must alias its
# live elements and mutation after the suspension is visible through the source
# list. Guards the lvalue-name disjunct of the borrow-alias classifier.
import asyncio


class Box:
    n: int

    def __init__(self, n: int) -> None:
        self.n = n


def first_two(items: list[Box]) -> tuple[Box, Box]:
    return (items[0], items[1])


async def f(items: list[Box]) -> int:
    t = first_two(items)
    a, b = t
    await asyncio.sleep(0)
    a.n += 10
    b.n += 20
    return a.n + b.n


async def amain() -> None:
    items = [Box(1), Box(2)]
    print(await f(items))
    print(items[0].n, items[1].n)


asyncio.run(amain())
