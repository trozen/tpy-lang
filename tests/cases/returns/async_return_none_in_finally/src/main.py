# Explicit `return None` routed through the CFG-finally pending slot
# (await inside finally) must emit the storage-form None.
import asyncio
from tpy import Int32


async def f(n: Int32) -> Int32 | None:
    try:
        if n > 0:
            return n
        return None
    finally:
        await asyncio.sleep(0)


async def main_coro() -> None:
    print(await f(5))
    print(await f(0))


asyncio.run(main_coro())
