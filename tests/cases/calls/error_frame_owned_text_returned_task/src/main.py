# A fresh f-string at a coroutine's structural `Iterable[char]` parameter is
# refused in the value of a `return` (or `yield`): the task outlives every
# seat of the returning coroutine (BUGS.md#task-outlives-spawning-frame-seat).
import asyncio
from typing import Iterable
from tpy import Own, char, int32


async def collect(xs: Iterable[char]) -> str:
    await asyncio.sleep(0)
    out = ""
    for c in xs:
        out += str(c)
    return out


async def spawn(n: int32) -> Own[asyncio.Task[str]]:  # tpyc: error(/call.arg_shape.protocol.static/)
    # the returned task keeps the f-string past `spawn`'s frame
    return asyncio.create_task(collect(f"abcdefghijklmnop_{n}_qrstuvwxyz"))


async def run() -> str:
    t = await spawn(12345)
    return await t


def main() -> None:
    print(asyncio.run(run()))


main()
