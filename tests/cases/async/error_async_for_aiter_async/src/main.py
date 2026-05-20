# `__aiter__` declared `async def` is rejected: Python 3.5.2+ requires
# the iterable's __aiter__ to be a sync method returning the async
# iterator. Only __anext__ is async.
import asyncio
from tpy import Own


class Iter:
    async def __anext__(self) -> int:
        raise StopAsyncIteration


class BadIterable:
    async def __aiter__(self) -> Own[Iter]:
        return Iter()


async def caller() -> None:
    async for x in BadIterable():  # tpyc: error(/`__aiter__`.*must be a sync `def`/)
        pass


def main() -> None:
    asyncio.run(caller())


main()
