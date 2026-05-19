# Basic async with: __aenter__/__aexit__ run, as-binding works.
import asyncio
from tpy import Int32


class CM:
    async def __aenter__(self) -> Int32:
        print("aenter")
        return 7

    async def __aexit__(self, exc_type: None, exc_val: None, exc_tb: None) -> None:
        print("aexit")


async def main_coro() -> None:
    async with CM() as v:
        print(v)


asyncio.run(main_coro())
