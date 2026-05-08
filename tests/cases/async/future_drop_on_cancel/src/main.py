# Future result must be dropped if its awaiter is cancelled after the
# result was set but before the awaiter consumed it. Validated by
# storing a `Tracked` value whose `__del__` prints -- the test asserts
# the destructor fires (catches both the debug-build panic on missing
# drop and the release-build silent leak).
import asyncio
from asyncio import Future


class Tracked:
    label: str

    def __init__(self, label: str) -> None:
        self.label = label

    def __del__(self) -> None:
        print("drop:", self.label)


async def waiter(f: Future[Tracked]) -> Tracked:
    return await f


async def main_coro() -> None:
    f: Future[Tracked] = Future[Tracked]()
    t = asyncio.create_task(waiter(f))
    f.set_result(Tracked("payload"))
    t.cancel()
    try:
        await t
    except asyncio.CancelledError:
        print("cancelled")


def main() -> None:
    asyncio.run(main_coro())


main()
