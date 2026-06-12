# An awaiting assert message is evaluated only on failure (CPython);
# the passing path must not run its side effects.
import asyncio
from tpy import Int32


async def msg(tag: str) -> str:
    print("building", tag)
    return tag + "!"


async def go(x: Int32) -> None:
    assert x > 0, await msg("positive")
    print("passed", x)


async def main_coro() -> None:
    await go(2)
    try:
        await go(0)
    except AssertionError as e:
        print("caught", e)


asyncio.run(main_coro())
