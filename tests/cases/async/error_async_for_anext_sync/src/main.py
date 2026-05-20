# `__anext__` declared sync (`def`) is rejected: async-for requires
# __anext__ to be `async def` so it returns an awaitable per iteration.
import asyncio
from tpy import Own


class BadIter:
    def __anext__(self) -> int:  # not async
        raise StopAsyncIteration


class Iterable:
    def __aiter__(self) -> Own[BadIter]:
        return BadIter()


async def caller() -> None:
    async for x in Iterable():  # tpyc: error(/`__anext__`.*must be `async def`/)
        pass


def main() -> None:
    asyncio.run(caller())


main()
