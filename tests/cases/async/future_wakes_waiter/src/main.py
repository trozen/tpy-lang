# A Future completed by a spawned task should wake the coroutine awaiting it.
# Cancelling the awaiting task cancels the Future, as in CPython, and so does
# a direct Future.cancel() while a waiter is parked.
import asyncio
from asyncio import Future, InvalidStateError
from tpy import int32


async def producer(f: Future[int32]) -> None:
    await asyncio.sleep(0.001)
    f.set_result(int32(7))


async def main_coro() -> None:
    f: Future[int32] = Future[int32]()
    asyncio.create_task(producer(f))
    result = await f
    print(result)


async def future_waiter(tag: str, f: Future[int32]) -> None:
    try:
        v = await f
        print("cancel:", tag, "got", v)
    except asyncio.CancelledError:
        print("cancel:", tag, "cancelled")
        raise


async def reap(tag: str, t: asyncio.Task[None]) -> None:
    try:
        await t
        print(tag, "not cancelled (WRONG)")
    except asyncio.CancelledError:
        print(tag, "saw the cancel")


async def cancel_wait() -> None:
    f: Future[int32] = Future[int32]()
    ta = asyncio.create_task(future_waiter("a", f))
    await asyncio.sleep(0.01)
    ta.cancel()  # tpyc: ok -- cancels the Future a waits on
    await reap("cancel:", ta)
    print("cancel: done", f.done())
    try:
        f.set_result(1)
        print("cancel: set_result accepted (WRONG)")
    except InvalidStateError:
        print("cancel: set_result rejected")
    tb = asyncio.create_task(future_waiter("b", f))
    await reap("cancel:", tb)


async def direct_cancel() -> None:
    f: Future[int32] = Future[int32]()
    t = asyncio.create_task(future_waiter("direct", f))
    await asyncio.sleep(0.01)
    f.cancel()  # tpyc: ok -- the parked waiter is woken and raises CancelledError
    print("direct: done", f.done())
    await reap("direct:", t)
    try:
        f.set_result(1)
        print("direct: set_result accepted (WRONG)")
    except InvalidStateError as e:
        print("direct: set_result rejected:", str(e))


def main() -> None:
    asyncio.run(main_coro())
    asyncio.run(cancel_wait())
    asyncio.run(direct_cancel())


main()
