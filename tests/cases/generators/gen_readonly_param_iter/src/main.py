# An explicit readonly[T] container param captured const in a resumable
# generator frame (the iterator slot must be a const_iterator), and the
# async-def route through the same const seeding: a narrowed-Optional self
# field iterated with awaits in the loop.

import asyncio
from typing import Iterator
from tpy import Int32, readonly


def tail(xs: readonly[list[Int32]]) -> Iterator[Int32]:
    yield -1
    for x in xs:
        yield x


class Holder:
    lst: list[Int32] | None

    def __init__(self):
        self.lst = [1, 2, 3]

    async def total(self) -> Int32:
        n = 0
        if self.lst is not None:
            for x in self.lst:
                await asyncio.sleep(0)
                n += x
        return n


async def amain() -> None:
    data: list[Int32] = [10, 20]
    print(sum(tail(data)))
    h = Holder()
    print(await h.total())


def main() -> None:
    asyncio.run(amain())


main()
