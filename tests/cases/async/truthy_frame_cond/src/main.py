# The coroutine half of the resumable-frame truthiness fix: an `async def`
# shares the generator's CFG decomposition, so a condition whose branch
# awaits took the same raw frame-field render.
import asyncio
from typing import Optional
from tpy import Int32


async def opt_branch(v: Int32 | None) -> Int32:
    if v:  # tpyc: warning(/Truthiness check on optional value/)
        await asyncio.sleep(0)
        return 1
    return 2


async def list_branch(xs: list[Int32]) -> Int32:
    if xs:
        await asyncio.sleep(0)
        return 1
    return 2


async def str_while(t: str) -> Int32:
    n = 0
    while t:
        await asyncio.sleep(0)
        n += 1
        t = ""
    return n


async def and_branch(xs: list[Int32], v: Int32 | None) -> Int32:
    # A boolop recurses into both operands, so each side takes its own
    # render -- a container length test and an optional truthiness test.
    if xs and v:  # tpyc: warning(/Truthiness check on optional value/)
        await asyncio.sleep(0)
        return 1
    return 2


async def main_async() -> None:
    print("zero", await opt_branch(0))
    print("none", await opt_branch(None))
    print("five", await opt_branch(5))
    print("list", await list_branch([1]), await list_branch([]))
    print("str", await str_while("x"), await str_while(""))
    print("and", await and_branch([1], 5), await and_branch([1], 0),
          await and_branch([], 5))


asyncio.run(main_async())
