# Happy path: inner coroutine completes within the deadline.
# `wait_for` should return the inner's value and not surface any
# exception. The deadline timer is left pending in the executor's
# heap on early return; the generation guard absorbs the late wake.
import asyncio


async def compute() -> int:
    await asyncio.sleep(0.001)
    return 42


async def main_coro() -> None:
    v = await asyncio.wait_for(compute(), 5.0)
    print(v)


def main() -> None:
    asyncio.run(main_coro())


main()
