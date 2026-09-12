# Nested defs inside async and generator METHODS (record-owned frames).
# The defs capture locals AND self: the async one mutates self.n from the
# closure (visible on the caller's object), the generator one reads self.n.
import asyncio
from typing import Iterator
from tpy import int32


class Counter:
    n: int32

    def __init__(self) -> None:
        self.n = 10

    async def bump_twice(self) -> int32:
        delta = 0

        def bump() -> None:
            nonlocal delta
            delta += 1
            self.n += delta

        bump()
        await asyncio.sleep(0)
        bump()
        return self.n

    def steps(self) -> Iterator[int32]:
        step = 100

        def next_offset() -> int32:
            nonlocal step
            step += self.n
            return step

        yield next_offset()
        yield next_offset()


def main() -> None:
    c = Counter()
    print(asyncio.run(c.bump_twice()))
    for v in c.steps():
        print(v)
    print(c.n)


main()
