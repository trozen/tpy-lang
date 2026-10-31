# `return await` of an owned tuple payload at a borrowing tuple return: the
# owned result dies at the return, so the forward is refused as in sync.
import asyncio
from tpy import int32, Own


class Box:
    def __init__(self, v: int32) -> None:
        self.v = v


async def owned(v: int32) -> tuple[int32, Own[Box]]:
    await asyncio.sleep(0)
    return (1, Box(v))


async def fwd(v: int32) -> tuple[int32, Box]:
    return await owned(v)  # tpyc: error(/Cannot return this tuple/)


async def amain() -> None:
    t = await fwd(3)
    print(t[0])


asyncio.run(amain())
