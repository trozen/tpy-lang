# A fresh f-string under an awaited call's argument is refused in a `return` value: the
# task the awaited call hands back outlives the frame (BUGS.md#task-outlives-spawning-frame-seat).
import asyncio
from typing import Iterable
from tpy import Own, char, int32


async def collect(xs: Iterable[char]) -> str:
    await asyncio.sleep(0)
    out = ""
    for c in xs:
        out += str(c)
    return out


async def identity(t: Own[asyncio.Task[str]]) -> Own[asyncio.Task[str]]:
    return t


async def spawn(n: int32) -> Own[asyncio.Task[str]]:  # tpyc: error(/call.arg_shape.protocol.static/)
    # only the awaited call's direct argument is used up in the statement; the task is not
    return await identity(asyncio.create_task(collect(f"abcdefghijklmnop_{n}_qrstuvwxyz")))


async def run() -> str:
    t = await spawn(7)
    return await t


def main() -> None:
    print(asyncio.run(run()))


main()
