# Async sibling of gen_proto_param_aliased_local: a static-protocol coroutine
# param aliased into a local (`xs = it`) then iterated across an await. The
# alias forwards to the captured param (no frame field of its own), reusing the
# deduced template arg T_it. The iterable + iterator are @nocopy, so a silent
# copy on the alias path -- not just a spelling regression -- is a compile error.
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
    xs = it
    for x in xs:
        await asyncio.sleep(0)
        print(x)


async def main_coro() -> None:
    named = Counter(0, 3)
    await consume(named)


def main() -> None:
    asyncio.run(main_coro())


main()
