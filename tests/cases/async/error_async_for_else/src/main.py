# `else:` clause on `async for` is rejected at parse time. Same
# restriction as `await` in `for ... else:` / `while ... else:` --
# the break-vs-normal-exit distinction is not modelled for async loops.
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
