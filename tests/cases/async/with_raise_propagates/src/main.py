# Body raises -> __aexit__ runs -> exception propagates out (cleanup-only
# v1.5 M5 path -- no suppression in scope).
import asyncio
from tpy import Int32


class CM:
    async def __aenter__(self) -> Int32:
        print("aenter")
        return 0

    async def __aexit__(self, exc_type: None, exc_val: None, exc_tb: None) -> None:
        print("aexit")


async def main_coro() -> None:
    try:
        async with CM() as v:
            print(v)
            raise ValueError("boom")
    except ValueError as e:
        print(f"caught: {e}")


asyncio.run(main_coro())
