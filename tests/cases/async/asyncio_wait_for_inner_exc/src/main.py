# Inner exception path: if the awaited coroutine raises a non-cancel
# exception, `wait_for` propagates it (not as TimeoutError). The
# deadline never fires here -- inner finishes first.
import asyncio


async def fails() -> int:
    await asyncio.sleep(0.001)
    raise ValueError("boom")


async def main_coro() -> None:
    try:
        v = await asyncio.wait_for(fails(), 5.0)
        print("not reached")
        print(v)
    except ValueError as e:
        print(e)


def main() -> None:
    asyncio.run(main_coro())


main()
