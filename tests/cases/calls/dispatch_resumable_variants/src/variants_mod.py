# Generator and coroutine @dispatch sets -- a method set, a free generator
# set, a free coroutine set -- defined in one module and called from another.
from typing import Iterator
from tpy import dispatch, int32


class Feed:
    seen: int32

    def __init__(self) -> None:
        self.seen = 0

    @dispatch
    def pull(self, x: int32) -> Iterator[int32]:
        self.seen += 1
        yield x * 10

    @dispatch
    def pull(self, x: str) -> Iterator[int32]:
        self.seen += 100
        yield len(x)


@dispatch
def ticks(n: int32) -> Iterator[int32]:
    yield n
    yield n * 2


@dispatch
def ticks(s: str) -> Iterator[int32]:
    yield len(s)


@dispatch
async def scaled(n: int32) -> int32:
    return n * 3


@dispatch
async def scaled(s: str) -> int32:
    return len(s) * 30
