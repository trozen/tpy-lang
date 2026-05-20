# Aiter record missing `__anext__` method is rejected -- __aiter__
# returned a type that can't supply the next element.
import asyncio
from tpy import Own


class NotAnIter:
    n: int

    def __init__(self) -> None:
        self.n = 0


class Iterable:
    def __aiter__(self) -> Own[NotAnIter]:
        return NotAnIter()


async def caller() -> None:
    async for x in Iterable():  # tpyc: error(/Async iterator.*missing `__anext__`/)
        pass


def main() -> None:
    asyncio.run(caller())


main()
