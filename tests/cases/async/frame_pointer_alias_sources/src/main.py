# A frame local bound by a for-loop over a list is a pointer alias of the
# element; `async with` / `async for` over it must use the element itself.
import asyncio
from tpy import Own, int32, nocopy


class Gate:
    entered: int32

    def __init__(self) -> None:
        self.entered = 0

    async def __aenter__(self) -> int32:
        self.entered += 1
        return self.entered

    async def __aexit__(self, a: None, b: None, c: None) -> None:
        pass


class Steps:
    limit: int32
    n: int32

    def __init__(self, limit: int32) -> None:
        self.limit = limit
        self.n = 0

    async def __anext__(self) -> int32:
        if self.n >= self.limit:
            raise StopAsyncIteration
        self.n += 1
        return self.n


# No copy of a source can be made, so the loop iterates the element itself.
@nocopy
class Source:
    limit: int32

    def __init__(self, limit: int32) -> None:
        self.limit = limit

    def __aiter__(self) -> Own[Steps]:
        return Steps(self.limit)


async def enter_each(gates: list[Gate]) -> int32:
    total = 0
    for g in gates:
        # The manager is the pointer-alias loop variable itself.
        async with g as n:
            total += n
    return total


async def iterate_each(sources: list[Source]) -> int32:
    total = 0
    for s in sources:
        # The iterable is the pointer-alias loop variable itself.
        async for x in s:
            total += x
    return total


async def main_coro() -> None:
    gates = [Gate(), Gate()]
    print("with:", await enter_each(gates))
    gates[0].entered += 10
    # The loop entered the list's own gates, so a second pass sees them.
    print("with again:", await enter_each(gates), gates[0].entered,
          gates[1].entered)
    sources = [Source(2), Source(3)]
    print("for:", await iterate_each(sources))
    sources[1].limit = 0
    print("for again:", await iterate_each(sources))


def main() -> None:
    asyncio.run(main_coro())


main()
