# A coroutine dropped mid-suspension while inside a `with` region still runs
# the manager's __exit__ (async sibling of iterators/gen_abandoned_with_exit;
# in TPy this exercises the executor cancel-drain + frame-destructor pair).
# No cross-task interleaving asserted: TPy's executor polls spawned tasks at
# drain, CPython at the next yield point.
import asyncio


class CM:
    def __enter__(self) -> None:
        print("enter")

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        print("exit exceptional" if exc_val is not None else "exit normal")


async def worker() -> None:
    cm = CM()
    with cm:
        await asyncio.sleep(10)


async def main_coro() -> None:
    t = asyncio.create_task(worker())
    await asyncio.sleep(0)


def main() -> None:
    asyncio.run(main_coro())
    print("after run")


main()
