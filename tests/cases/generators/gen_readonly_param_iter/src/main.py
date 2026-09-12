# An explicit readonly[T] container param captured const in a resumable
# generator frame (the iterator slot must be a const_iterator), and the
# async-def route through the same const seeding: a narrowed-Optional self
# field iterated with awaits in the loop.

import asyncio
from typing import Iterator
from tpy import int32, readonly


def tail(xs: readonly[list[int32]]) -> Iterator[int32]:
    yield -1
    for x in xs:
        yield x


class Holder:
    lst: list[int32] | None

    def __init__(self):
        self.lst = [1, 2, 3]

    async def total(self) -> int32:
        n = 0
        if self.lst is not None:
            for x in self.lst:
                await asyncio.sleep(0)
                n += x
        return n


async def amain() -> None:
    data: list[int32] = [10, 20]
    print(sum(tail(data)))
    h = Holder()
    print(await h.total())


def main() -> None:
    # Rvalue arg: materialized as a named scope-local so the frame's const
    # borrow outlives the statement -- including a generator held across
    # statements.
    print(sum(tail([1, 2])))
    g = tail([3, 4])
    total = 0
    for x in g:
        total += x
    print(total)
    asyncio.run(amain())


main()
