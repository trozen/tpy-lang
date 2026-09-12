# A Future completed by a spawned task should wake the coroutine awaiting it.
import asyncio
from asyncio import Future
from tpy import int32


async def producer(f: Future[int32]) -> None:
    await asyncio.sleep(0.001)
    f.set_result(int32(7))


async def main_coro() -> None:
    f: Future[int32] = Future[int32]()
    asyncio.create_task(producer(f))
    result = await f
    print(result)


def main() -> None:
    asyncio.run(main_coro())


main()
