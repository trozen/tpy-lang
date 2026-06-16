# The start_server shape: an `async def` handler passed directly, stored as a
# Callable field, and invoked per item from a method via create_task. The
# handler takes Own[@nocopy] -- the value moves through the synthesized factory
# wrapper, so a silent copy would be a compile error (forces the move path).
import asyncio
from typing import Callable
from tpy import Int32, Own, nocopy
from tpy.coro import Cancellable


@nocopy
class Conn:
    id: Int32

    def __init__(self, id: Int32) -> None:
        self.id = id


async def handle(c: Own[Conn]) -> None:
    await asyncio.sleep(0.0)
    print("handled " + str(c.id))


@nocopy
class Dispatcher:
    _cb: Callable[[Own[Conn]], Own[Cancellable[None]]]

    def __init__(self,
                 cb: Callable[[Own[Conn]], Own[Cancellable[None]]]) -> None:
        self._cb = cb

    async def run(self, count: Int32) -> None:
        tasks: list[asyncio.Task[None]] = []
        i: Int32 = 0
        while i < count:
            tasks.append(asyncio.create_task(self._cb(Conn(i))))
            i += 1
        for t in tasks:
            await t


async def main_coro() -> None:
    d = Dispatcher(handle)
    await d.run(3)


def main() -> None:
    asyncio.run(main_coro())


main()
