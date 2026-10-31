# `return await` of a borrowing tuple rooted in the forwarder's own local
# would dangle, so it is refused as the sync relay of the same call is.
import asyncio
from tpy import int32


class Box:
    def __init__(self, v: int32) -> None:
        self.v = v


async def pair(b: Box) -> tuple[int32, Box]:
    await asyncio.sleep(0)
    return (1, b)


async def fwd() -> tuple[int32, Box]:
    b = Box(41)
    return await pair(b)  # tpyc: error(/Cannot return this tuple/)


async def amain() -> None:
    t = await fwd()
    print(t[0])


asyncio.run(amain())
