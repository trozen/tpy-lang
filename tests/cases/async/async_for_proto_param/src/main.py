# Async coroutine that for-loops over a static-protocol param across an await.
# The coro captures the param as the deduced template arg T_it and types the
# for-loop iterator frame field against it (the C++ concept rendering is
# un-instantiable). The @nocopy iterable forces the value-vs-reference
# distinction: the coro must BORROW the param (a silent copy is a compile
# error). Driven via asyncio.run (Adapter-wrapped).
import asyncio
from typing import Iterable
from tpy import int32, Own, nocopy


class CounterIter:
    current: int32
    limit: int32

    def __init__(self, start: int32, limit: int32) -> None:
        self.current = start
        self.limit = limit

    def __next__(self) -> int32:
        if self.current < self.limit:
            result = self.current
            self.current += 1
            return result
        raise StopIteration


@nocopy
class Counter:
    start: int32
    limit: int32

    def __init__(self, start: int32, limit: int32) -> None:
        self.start = start
        self.limit = limit

    def __iter__(self) -> Own[CounterIter]:
        return CounterIter(self.start, self.limit)


async def consume(it: Iterable[int32]) -> None:
    for x in it:
        await asyncio.sleep(0)
        print(x)


def main() -> None:
    c = Counter(0, 3)
    asyncio.run(consume(c))


main()
