# Two sequential `await asyncio.wait_for(...)` calls in the same
# async function. Each await uses a separate `__sub_N` slot in the
# coro frame; the cancel-check helper runs on both. Validates that
# the second wait_for's sub-coro field (which carries a deduced
# `T_coro` template arg derived from a different free-function call)
# is emitted with the right per-call qualification.
import asyncio


async def first() -> int:
    await asyncio.sleep(0.001)
    return 10


async def second() -> int:
    await asyncio.sleep(0.001)
    return 20


async def main_coro() -> None:
    a = await asyncio.wait_for(first(), 5.0)
    b = await asyncio.wait_for(second(), 5.0)
    print(a + b)


def main() -> None:
    asyncio.run(main_coro())


main()
