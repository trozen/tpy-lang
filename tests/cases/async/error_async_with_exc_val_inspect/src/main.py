# v1.5 M5 limitation: `exc_val: Optional[BaseException]` on __aexit__
# is not yet supported (needs polymorphic exception storage across
# the suspension, tracked as E9 / Phase 20).
import asyncio
from typing import Optional


class CM:
    async def __aenter__(self) -> int:
        return 1

    async def __aexit__(self, exc_type: None, exc_val: Optional[BaseException], exc_tb: None) -> None:
        pass


async def main_coro() -> None:
    async with CM() as v:  # tpyc: error(/`__aexit__` with `exc_val: Optional\[BaseException\]` is not yet supported/)
        print(v)


asyncio.run(main_coro())
