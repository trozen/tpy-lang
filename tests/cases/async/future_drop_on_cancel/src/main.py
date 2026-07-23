# Future result must be dropped if its awaiter is cancelled after the
# result was set but before the awaiter consumed it. Validated by
# storing a `Tracked` value whose `__del__` records into a module-level
# list; the test asserts the destructor fires (catches both the
# debug-build panic on missing drop and the release-build silent leak).
#
# We verify drop occurred but don't pin the ordering of "drop" vs
# "cancelled" prints: TPy and CPython sequence destructor execution
# differently (RAII-immediate at throw vs deferred decref at frame
# teardown), and the test's purpose is "destructor fires, no leak"
# regardless of exact timing.
import asyncio
from tpy import Own
from asyncio import Future


dropped: list[str] = []


class Tracked:
    label: str

    def __init__(self, label: str) -> None:
        self.label = label

    def __del__(self) -> None:
        dropped.append(self.label)


async def waiter(f: Future[Tracked]) -> Own[Tracked]:
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
    # By here every coroutine frame has torn down. Tracked must have
    # dropped exactly once.
    if "payload" in dropped:
        print("dropped:", "payload")
    print("count:", len(dropped))


main()
