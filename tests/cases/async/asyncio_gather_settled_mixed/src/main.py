# asyncio.gather_list_settled: mixed success + exception case. A failing
# sibling does NOT cancel the others (unlike gather_list); each task runs to
# completion and contributes its own Settled entry. The exception entry is
# re-raised and caught as `ValueError` (NOT bare `BaseException`) to verify
# the dynamic subclass survives the Box[Throwable] round-trip through gather --
# a slice-to-BaseException regression would miss the `except ValueError` arm
# and hit the fallback.
import asyncio
from tpy import int32


async def good(n: int32) -> int32:
    await asyncio.sleep(0.001)
    return n


async def bad() -> int32:
    await asyncio.sleep(0.001)
    raise ValueError("boom")


async def main_coro() -> None:
    tasks: list[asyncio.Task[int32]] = []
    tasks.append(asyncio.create_task(good(int32(1))))
    tasks.append(asyncio.create_task(bad()))
    tasks.append(asyncio.create_task(good(int32(3))))
    results = await asyncio.gather_list_settled(tasks)
    for r in results:
        if r.exception is not None:
            try:
                raise r.exception
            except ValueError as v:
                print("valueerror:", v.message)
            except BaseException as e:
                print("WRONG-TYPE:", e.message)
        elif r.value is not None:
            print("ok:", r.value.get())


def main() -> None:
    asyncio.run(main_coro())


main()
