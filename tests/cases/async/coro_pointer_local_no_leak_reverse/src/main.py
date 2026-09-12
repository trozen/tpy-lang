# Regression: symmetric to coro_pointer_local_no_leak -- here the
# value-typed `x` comes first, then the pointer-form Optional[NonValue]
# `x`. Exercises the save/restore branch where the second function's
# entry sees an empty `pointer_locals` from its own clear (rather than
# inheriting stale entries from a previous body) but still needs the
# setup helper to register the pointer-form name.
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


async def first(n: int32) -> int32:
    x = n + 1  # tpyc: type(int32)
    await asyncio.sleep(0)
    return x


async def second(items: list[P], i: int32) -> int32:
    x = maybe_p(items, i)  # tpyc: type(P | None)
    await asyncio.sleep(0)
    if x is not None:
        return x.v
    return -1


async def driver() -> None:
    items: list[P] = []
    items.append(P(42))
    print(await first(10))
    print(await second(items, 0))


asyncio.run(driver())
