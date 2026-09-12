# Regression: with-statement `as`-binding in an async coro body where
# `__enter__` returns a reference-type value. Codegen emits
# `auto& it = __ctx_N.__enter__();` -- a C++-scoped reference. Sema
# adds `it` to `func.generator_locals` -> `generator_optional_fields`.
# Before the fix, body-emit's `(*it)` peel mis-fired on `it.n`.
import asyncio
from tpy import int32


class Item:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class CM:
    item: Item

    def __init__(self, n: int32) -> None:
        self.item = Item(n)

    def __enter__(self) -> Item:
        return self.item

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


async def total() -> int32:
    s: int32 = 0
    await asyncio.sleep(0)
    with CM(42) as it:
        s += it.n
    return s


async def driver() -> None:
    print(await total())


asyncio.run(driver())
