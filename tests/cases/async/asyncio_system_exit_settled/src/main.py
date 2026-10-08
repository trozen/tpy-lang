# A sys.exit in a child of asyncio.gather_list_settled leaves the run: it is
# not collected as a Settled entry, unlike an ordinary exception.
import asyncio
import sys
from tpy import int32


async def exiter() -> int32:
    await asyncio.sleep(0.001)
    # The subject: the exit is not a settled outcome.
    sys.exit(7)  # tpyc: ok
    return 0


async def failer() -> int32:
    raise ValueError("boom")


async def sibling() -> int32:
    await asyncio.sleep(0.05)
    return 1


async def main_settles() -> None:
    tasks: list[asyncio.Task[int32]] = []
    tasks.append(asyncio.create_task(failer()))
    tasks.append(asyncio.create_task(exiter()))
    tasks.append(asyncio.create_task(sibling()))
    results = await asyncio.gather_list_settled(tasks)
    print("settled: main continued (wrong)", len(results))


def main() -> None:
    try:
        asyncio.run(main_settles())
    except SystemExit as e:
        print("settled: out of run", e.code)


main()
