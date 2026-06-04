# Awaiting a coroutine that takes a static-protocol parameter, with the
# iterable forwarded through another coroutine (driver -> await consume).
# The sub-future field in the awaiting coro is typed as the callee factory's
# exact forwarding-deduced type, so a named (lvalue) iterable BORROWS rather
# than copies. The @nocopy iterable forces that: a silent copy anywhere along
# the await chain would be a compile error.
import asyncio
from typing import Iterable
from tpy import Int32, Own, nocopy


class CounterIter:
    current: Int32
    limit: Int32

    def __init__(self, start: Int32, limit: Int32) -> None:
        self.current = start
        self.limit = limit

    def __next__(self) -> Int32:
        if self.current < self.limit:
            result = self.current
            self.current += 1
            return result
        raise StopIteration


@nocopy
class Counter:
    start: Int32
    limit: Int32

    def __init__(self, start: Int32, limit: Int32) -> None:
        self.start = start
        self.limit = limit

    def __iter__(self) -> Own[CounterIter]:
        return CounterIter(self.start, self.limit)


async def consume(it: Iterable[Int32]) -> None:
    for x in it:
        await asyncio.sleep(0)
        print(x)


async def driver(it: Iterable[Int32]) -> None:
    await consume(it)


def main() -> None:
    c = Counter(0, 3)
    asyncio.run(driver(c))


main()
