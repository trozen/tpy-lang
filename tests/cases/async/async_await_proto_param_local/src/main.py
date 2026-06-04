# Awaiting a protocol-param coroutine with a hoisted-local iterable arg.
# The local is stored in the resumable frame as a `frame_slot`, so the
# sub-future field type must spell the arg with the same body-context deref the
# emplace uses (`*named`), not the bare `frame_slot` wrapper. Both the iterable
# (captured into the sub-future) and the iterator (stored in the for-loop's
# `__for_itr` frame slot) are @nocopy, so a silent copy on either path -- the
# borrow capture or the iterator storage -- is a compile error, not just a
# spelling regression.
import asyncio
from typing import Iterable
from tpy import Int32, Own, nocopy


@nocopy
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


async def main_coro() -> None:
    named = Counter(0, 3)
    await consume(named)


def main() -> None:
    asyncio.run(main_coro())


main()
