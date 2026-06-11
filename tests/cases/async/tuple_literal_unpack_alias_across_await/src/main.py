# A tuple-literal unpack of reference elements in an async body, with the
# aliases held across an await, must still alias (observe the mutation),
# not copy -- the desugared single-assigns survive the suspension as frame
# pointer slots.
import asyncio
from tpy import Int32


class Counter:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n


async def work(items: list[Counter]) -> None:
    a, b = (items[0], items[1])
    await asyncio.sleep(0.01)
    a.n += 10
    print(items[0].n)
    print(b.n)


async def amain() -> None:
    items = [Counter(1), Counter(2)]
    await work(items)


asyncio.run(amain())
