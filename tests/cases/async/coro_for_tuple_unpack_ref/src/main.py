# Regression: tuple-unpack iter var in an async coro body iterating
# `list[tuple[Item, Item]]` (reference-type elements). Codegen emits
# `T& a = std::get<0>(...); T& b = std::get<1>(...);` -- C++-scoped
# references. Sema added `a`, `b` to `func.generator_locals` ->
# `generator_optional_fields`. Before the fix, body-emit's `(*a)` /
# `(*b)` peel mis-fired on `a.n` / `b.n`.
import asyncio
from tpy import int32


class Item:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


async def total(pairs: list[tuple[Item, Item]]) -> int32:
    s: int32 = 0
    await asyncio.sleep(0)
    for a, b in pairs:
        s += a.n + b.n
    return s


async def driver() -> None:
    pairs: list[tuple[Item, Item]] = []
    pairs.append((Item(1), Item(2)))
    pairs.append((Item(10), Item(20)))
    print(await total(pairs))


asyncio.run(driver())
