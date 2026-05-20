# Iterable record missing `__aiter__` method is rejected -- the
# loop's iteration setup can't find the async iterator.
import asyncio


class NotIterable:
    n: int

    def __init__(self) -> None:
        self.n = 0


async def caller() -> None:
    async for x in NotIterable():  # tpyc: error(/cannot be used as an async iterable.*missing __aiter__/)
        pass


def main() -> None:
    asyncio.run(caller())


main()
