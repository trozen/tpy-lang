# A tuple-unpack loop over a USER-defined iterator across a suspension: the
# iter_next strategy never records the per-loop borrow-tuple fact its holder
# would need.
import asyncio
from tpy import Int32, Own


class Box:
    v: Int32

    def __init__(self, v: Int32) -> None:
        self.v = v


class Pairs:
    i: Int32

    def __init__(self) -> None:
        self.i = 0

    def __iter__(self) -> "Pairs":
        return self

    def __next__(self) -> tuple[Int32, Own[Box]]:
        self.i = self.i + 1
        if self.i > 2:
            raise StopIteration
        return (self.i, Box(self.i))


async def step(n: Int32) -> Int32:
    return n + 1


async def f() -> Int32:  # tpyc: error(/res\.local_storage/)
    total = 0
    # The unpack holder for a user iterator has no frame form.
    for k, b in Pairs():
        total = await step(k + b.v)
    return total


def main() -> None:
    print(asyncio.run(f()))


main()
