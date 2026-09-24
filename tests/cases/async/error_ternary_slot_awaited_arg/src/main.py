# A mixed ternary passed to an awaited coroutine: the fresh arm's slot would
# die at the suspension while the coroutine still reads its parameter, so the
# argument is refused (BUGS.md#reference-ternary-position-gaps).
import asyncio
from tpy import int32, Own


class C:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def make(k: int32) -> Own[C]:
    return C(k)


async def worker(x: C) -> int32:
    await asyncio.sleep(0)
    return x.n


# A resumable body's reject is located at its `def`.
async def amain(c: bool) -> None:  # tpyc: error(/expr\.ifexpr/)
    a = C(1)
    r = await worker(a if c else make(5))
    print(r)


asyncio.run(amain(False))
