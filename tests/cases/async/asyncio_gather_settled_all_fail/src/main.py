# asyncio.gather_list_settled: every task raises. Exercises the assembly
# path where `_result_indices` stays empty and only `_exc_indices` is
# populated -- the result is N Settled entries all with `exception` set.
# Distinct exception messages per index verify the arrival-order arrays
# reassemble by input index correctly.
import asyncio
from tpy import Int32


async def fail(tag: Int32) -> Int32:
    await asyncio.sleep(0.001)
    raise ValueError(f"boom-{tag}")


async def main_coro() -> None:
    tasks: list[asyncio.Task[Int32]] = []
    tasks.append(asyncio.create_task(fail(Int32(0))))
    tasks.append(asyncio.create_task(fail(Int32(1))))
    tasks.append(asyncio.create_task(fail(Int32(2))))
    results = await asyncio.gather_list_settled(tasks)
    print("count", len(results))
    for r in results:
        if r.exception is not None:
            try:
                raise r.exception
            except BaseException as e:
                print("exc:", e.message)
        elif r.value is not None:
            print("unexpected ok:", r.value.get())


def main() -> None:
    asyncio.run(main_coro())


main()
