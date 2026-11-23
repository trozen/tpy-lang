# What part_frame_order calls across the module boundary: a coroutine
# taking an iterator, a generic generator, and the record it is given.
import asyncio
from typing import Iterator
from tpy import int32


class Rec:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


async def total(it: Iterator[int32]) -> int32:
    t = 0
    for x in it:
        await asyncio.sleep(0)
        t += x
    return t


def gen_lib[T](x: T) -> Iterator[T]:
    yield x


def consume_rec(g: Iterator[Rec]) -> int32:
    t = 0
    for r in g:
        t += r.n
    return t
