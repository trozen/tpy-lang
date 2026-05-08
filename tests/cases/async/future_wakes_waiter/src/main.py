# A Future completed by a spawned task should wake the coroutine awaiting it.
import asyncio
from asyncio import Future
from tpy import Int32


async def producer(f: Future[Int32]) -> None:
    await asyncio.sleep(0.001)
    f.set_result(Int32(7))


async def main_coro() -> None:
    f: Future[Int32] = Future[Int32]()
    asyncio.create_task(producer(f))
    result = await f
    print(result)


def main() -> None:
    asyncio.run(main_coro())


main()
