# A tuple-unpack loop in a coroutine that mutates a reference element AFTER
# an await inside the loop body. The
# reference target must alias the live container element (frame-stored
# pointer-form) so the mutation propagates to the source -- the shared
# `_gen_tuple_unpack` frame path covers generators and coroutines alike.
import asyncio
from tpy import Int32


class Item:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n


async def process(rows: list[tuple[Int32, Item]]) -> Int32:
    total = 0
    for idx, it in rows:
        await asyncio.sleep(0)
        it.n = idx * 10       # mutate after the await; `it` must alias + survive
        total += it.n
    return total


async def amain() -> None:
    rows: list[tuple[Int32, Item]] = [(1, Item(0)), (2, Item(0))]
    print(await process(rows))
    print(rows[0][1].n, rows[1][1].n)


asyncio.run(amain())
