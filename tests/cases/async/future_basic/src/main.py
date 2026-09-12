import asyncio
from asyncio import Future
from tpy import int32

async def main_coro(f: Future[int32]) -> None:
    val = await f
    print(val)

async def amain() -> None:
    # Construct the Future inside the running loop: CPython >= 3.14 rejects
    # loop-less Future() construction (get_event_loop() errors with no running
    # loop), so create + set_result within asyncio.run's loop.
    f: Future[int32] = Future[int32]()
    f.set_result(int32(99))
    await main_coro(f)

def main() -> None:
    asyncio.run(amain())

main()
