# Binding an async def with a static-protocol param: its frame is a
# template whose struct name needs call-site deduction, so the binding
# falls back to the ERASED handle (unique_ptr) -- the documented
# signature-visible boundary. Consumption works the same.
import asyncio
from typing import Iterable


async def total(items: Iterable[int]) -> int:
    s = 0
    for x in items:
        s += x
    return s


async def main_coro() -> None:
    xs = [1, 2, 3]
    c = total(xs)
    print(await c)


def main() -> None:
    asyncio.run(main_coro())


main()
