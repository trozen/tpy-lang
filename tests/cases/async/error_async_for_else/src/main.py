# `else:` clause on `async for` is rejected at parse time. Sync
# `for`/`while`-`else` with a suspension IS supported (the CFG models
# break-vs-normal-exit), but the `__anext__`-driven async-for advance
# doesn't yet route its normal-exit edge through an else region.
import asyncio
from tpy import Own


class Empty:
    async def __anext__(self) -> int:
        raise StopAsyncIteration


class Iterable:
    def __aiter__(self) -> Own[Empty]:
        return Empty()


async def caller() -> None:
    async for x in Iterable():  # tpyc: error(/`else:`.*`async for`.*not supported/)
        pass
    else:
        pass


def main() -> None:
    asyncio.run(caller())


main()
