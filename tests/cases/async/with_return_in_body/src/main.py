# `return` inside async-with body: walks through __aexit__ on the
# way out (M3.3.2 pending-return mechanism shared with try/finally).
import asyncio


class CM:
    async def __aenter__(self) -> int:
        print("aenter")
        return 42

    async def __aexit__(self, exc_type: None, exc_val: None, exc_tb: None) -> None:
        print("aexit")


async def inner() -> int:
    async with CM() as v:
        print(v)
        return v + 100


async def main_coro() -> None:
    r = await inner()
    print(r)


asyncio.run(main_coro())
