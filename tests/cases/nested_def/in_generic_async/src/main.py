# Async function with a protocol-typed param (templated frame) holding
# multiple nested defs, one taking a reference-type param.
import asyncio
from typing import Iterable
from tpy import int32, Own


class Box:
    n: int32

    def __init__(self) -> None:
        self.n = 10


async def combine(xs: Iterable[int32]) -> Own[Box]:
    b = Box()

    def stash(target: Box, extra: int32) -> None:
        target.n += extra

    def double() -> None:
        nonlocal b
        b.n *= 2

    for x in xs:
        stash(b, x)
    await asyncio.sleep(0)
    double()
    return b  # tpyc: warning(/copies Box into owned storage/) -- double() captures b


async def main() -> None:
    xs = [5, 1]
    print((await combine(xs)).n)


asyncio.run(main())
