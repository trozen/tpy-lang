# `async for x in g:` over a module-global async iterable. A global
# renders as `Src*`, so the `.__aiter__()` call must deref it rather
# than hitting `.`-on-pointer. Same re-address root as the global
# await / with-manager cases.
import asyncio
from tpy import int32, Own


class AIter:
    n: int32
    limit: int32

    def __init__(self, limit: int32) -> None:
        self.n = 0
        self.limit = limit

    async def __anext__(self) -> int32:
        if self.n >= self.limit:
            raise StopAsyncIteration
        self.n += 1
        return self.n


class Source:
    limit: int32

    def __init__(self, limit: int32) -> None:
        self.limit = limit

    def __aiter__(self) -> Own[AIter]:
        return AIter(self.limit)


g = Source(3)


async def main_coro() -> None:
    total = 0
    async for x in g:
        total += x
    print(total)


def main() -> None:
    asyncio.run(main_coro())


main()
