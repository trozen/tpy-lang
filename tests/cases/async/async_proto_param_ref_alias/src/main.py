# The async sibling of iterators/gen_proto_param_ref_alias: a coroutine frame
# holds hoisted for-loop vars the same way a generator frame does, so a loop
# element over a protocol-typed iterable must alias rather than copy across the
# suspension -- the mutation has to reach the caller's container.
import asyncio
from tpy import int32
from typing import Iterable


class Point:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


async def bump(items: Iterable[Point]) -> int32:
    total = 0
    for p in items:
        p.x += 100
        await asyncio.sleep(0)
        total += p.x
    return total


async def amain() -> None:
    pts = [Point(1), Point(2)]
    got = await bump(pts)
    print("total:", got)
    print("mutations reached the caller:", pts[0].x, pts[1].x)


asyncio.run(amain())
