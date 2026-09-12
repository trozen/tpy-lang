# Regression: two async functions in one module must not share
# `pointer_locals` state across body emission. Before the fix, the
# pointer-form Optional[NonValue] local `x` in `first` was registered
# in `pointer_locals` by the var-decl bridge and never cleared; the
# value-typed `x` in `second` then read as `(*x)` (hard C++ build
# error: `invalid type argument of unary '*'`).
import asyncio
from tpy import nocopy, int32


@nocopy
class P:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


def maybe_p(items: list[P], i: int32) -> P | None:
    if i < len(items):
        return items[i]
    return None


async def first(items: list[P], i: int32) -> int32:
    x = maybe_p(items, i)  # tpyc: type(P | None)
    await asyncio.sleep(0)
    if x is not None:
        return x.v
    return -1


async def second(n: int32) -> int32:
    x = n + 1  # tpyc: type(int32)
    await asyncio.sleep(0)
    return x


async def driver() -> None:
    items: list[P] = []
    items.append(P(42))
    print(await first(items, 0))
    print(await second(10))


asyncio.run(driver())
