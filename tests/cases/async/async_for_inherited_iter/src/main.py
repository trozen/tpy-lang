# Regression: `async for` over a subclass inheriting __aiter__/__anext__ must
# find them (MRO lookup) and name the __anext__ coro struct after the defining
# base.
import asyncio
from tpy import Int32


class BaseAIter:
    i: Int32

    def __init__(self) -> None:
        self.i = 0

    def __aiter__(self) -> "BaseAIter":
        return self

    async def __anext__(self) -> Int32:
        await asyncio.sleep(0)
        if self.i >= 3:
            raise StopAsyncIteration()
        self.i += 1
        return self.i


class DerivedAIter(BaseAIter):
    pass


async def main_coro() -> None:
    total: Int32 = 0
    async for x in DerivedAIter():
        total += x
    print(total)


def main() -> None:
    asyncio.run(main_coro())


main()
