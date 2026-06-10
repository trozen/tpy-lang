# Mixed value+reference tuple unpack from a call (borrow-tuple source) held
# across an await: the value element becomes a plain value frame field, the
# reference element a `Box*` alias of the live source. Mutating the reference
# after the await is visible through the list; the value element survives too.
import asyncio


class Box:
    n: int

    def __init__(self, n: int) -> None:
        self.n = n


def pick(items: list[Box], i: int) -> tuple[int, Box]:
    return (i * 100, items[i])


async def step(items: list[Box]) -> int:
    tag, it = pick(items, 0)
    await asyncio.sleep(0)
    it.n += 5
    return tag + it.n


async def amain() -> None:
    items = [Box(1)]
    print(await step(items))
    print(items[0].n)


asyncio.run(amain())
