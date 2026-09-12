# Regression: async function with pointer-form Optional[NonValue] param.
# Frame slot + factory signature must use `T*` (matching sync), not
# `std::optional<T>&`; the call site must apply `&` lift on the arg to
# match the new factory shape.
import asyncio
from tpy import nocopy, int32


@nocopy
class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


async def takes_optional(p: P | None) -> int32:
    if p is not None:
        return p.n
    return -1


async def driver() -> None:
    items: list[P] = []
    items.append(P(42))
    items.append(P(7))
    print(await takes_optional(items[0]))
    print(await takes_optional(items[1]))
    print(await takes_optional(None))


asyncio.run(driver())
