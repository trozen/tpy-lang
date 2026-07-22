# Nested defs inside async and generator METHODS (record-owned frames).
# The defs capture locals only: self access from a nested def is a
# pre-existing unsupported shape in sync and resumable forms alike (BUGS.md).
import asyncio
from typing import Iterator
from tpy import Int32


class Counter:
    n: Int32

    def __init__(self) -> None:
        self.n = 10

    async def bump_twice(self) -> Int32:
        delta = 0

        def bump() -> None:
            nonlocal delta
            delta += 1

        bump()
        await asyncio.sleep(0)
        bump()
        self.n += delta
        return self.n

    def steps(self) -> Iterator[Int32]:
        step = 100

        def next_offset() -> Int32:
            nonlocal step
            step += 100
            return step

        yield self.n + next_offset()
        yield self.n + next_offset()


def main() -> None:
    c = Counter()
    print(asyncio.run(c.bump_twice()))
    for v in c.steps():
        print(v)


main()
