# Single-assign reference alias held across an await: `a` must stay a `T*`
# alias of the live list element (not a value copy), so mutating it after the
# suspension is visible through the source list.
import asyncio


class Box:
    n: int

    def __init__(self, n: int) -> None:
        self.n = n


async def bump(items: list[Box]) -> int:
    a = items[0]
    await asyncio.sleep(0)
    a.n += 10
    return a.n


async def amain() -> None:
    items = [Box(1)]
    print(await bump(items))
    print(items[0].n)


asyncio.run(amain())
