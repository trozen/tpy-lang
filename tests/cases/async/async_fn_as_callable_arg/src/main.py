# An `async def` passed as a value to a `Callable[[...], Own[Cancellable[T]]]`
# param is a coroutine factory: calling it builds a coro that create_task drives.
# Codegen synthesizes a wrapper that adapts the frame into the owning handle.
import asyncio
from typing import Callable
from tpy import Int32, Own
from tpy.coro import Cancellable


async def double(n: Int32) -> Int32:
    await asyncio.sleep(0.0)
    return n + n


async def run_twice(factory: Callable[[Int32], Own[Cancellable[Int32]]],
                    a: Int32, b: Int32) -> Int32:
    t1 = asyncio.create_task(factory(a))  # tpyc: ok
    t2 = asyncio.create_task(factory(b))
    return await t1 + await t2


async def main_coro() -> Int32:
    return await run_twice(double, 3, 5)


def main() -> None:
    print(asyncio.run(main_coro()))


main()
