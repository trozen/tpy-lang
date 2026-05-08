import asyncio
from asyncio import Future
from tpy import Int32

async def main_coro(f: Future[Int32]) -> None:
    val = await f
    print(val)

def main() -> None:
    f: Future[Int32] = Future[Int32]()
    f.set_result(Int32(99))
    asyncio.run(main_coro(f))

main()
