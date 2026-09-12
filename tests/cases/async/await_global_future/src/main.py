# `await` of a module-global awaitable. A global is accessed through a
# pointer slot, so the await borrow must not re-take its address. The
# Future is completed by a spawned task so the await resolves.
import asyncio
from asyncio import Future
from tpy import int32

done: Future[int32] = Future[int32]()


async def producer() -> None:
    done.set_result(42)


async def main_coro() -> None:
    _t = asyncio.create_task(producer())
    x = await done
    print(x)


def main() -> None:
    asyncio.run(main_coro())


main()
