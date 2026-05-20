# `async for` is rejected outside an `async def` body, same as `await`.
from tpy import Own


class Empty:
    async def __anext__(self) -> int:
        raise StopAsyncIteration


class Iterable:
    def __aiter__(self) -> Own[Empty]:
        return Empty()


def caller() -> None:
    async for x in Iterable():  # tpyc: error(/`async for` is only allowed inside an `async def`/)
        pass


caller()
