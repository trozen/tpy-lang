# A generator and a coroutine another module calls through its module name.
import asyncio
from typing import Iterator
from tpy import int32


class Src:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


async def co(s: Src) -> int32:
    await asyncio.sleep(0)
    s.n += 1
    return s.n


def gen(s: Src) -> Iterator[int32]:
    yield s.n
    s.n += 100
    yield s.n
