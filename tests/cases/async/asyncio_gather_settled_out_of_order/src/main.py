# asyncio.gather_list_settled: results returned in INPUT order even when
# sub-tasks SETTLE in a different (non-monotonic) order. Sleep durations are
# inverted vs input position (index 0 sleeps longest, index 2 shortest), so
# the arrival-order `_result_indices` array fills as [2, 1, 0] -- the assembly
# walk must reorder back to input order [0, 1, 2]. The other settled tests use
# uniform sleeps (arrival == input), so none of them actually stress the
# input-order reassembly with a permuted arrival array. Deadlines are absolute
# (executor min-heap), so the settle order is deterministic, not load-sensitive.
import asyncio
from tpy import Int32


async def fetch(n: Int32, delay: float) -> Int32:
    await asyncio.sleep(delay)
    return n


async def main_coro() -> None:
    tasks: list[asyncio.Task[Int32]] = []
    tasks.append(asyncio.create_task(fetch(Int32(0), 0.005)))   # settles last
    tasks.append(asyncio.create_task(fetch(Int32(1), 0.003)))   # settles middle
    tasks.append(asyncio.create_task(fetch(Int32(2), 0.001)))   # settles first
    results = await asyncio.gather_list_settled(tasks)
    for r in results:
        if r.value is not None:
            print("ok:", r.value.get())
        else:
            print("unexpected exc-slot")


def main() -> None:
    asyncio.run(main_coro())


main()
