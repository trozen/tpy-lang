# Nested defs in an async def are frame members: they capture frame-field
# locals (mutation visible after the call) and stay callable across awaits.
import asyncio
from typing import Callable
from tpy import int32, Own


class Box:
    n: int32

    def __init__(self) -> None:
        self.n = 10


async def capture_mutate() -> Own[Box]:
    b = Box()

    def bump() -> None:
        nonlocal b
        b.n += 1

    bump()
    await asyncio.sleep(0)
    bump()
    return b


async def across_await() -> int32:
    base = 100

    def scaled(x: int32) -> int32:
        return x + base

    first = scaled(1)
    await asyncio.sleep(0)
    return first + scaled(2)


def apply(f: Callable[[int32], int32], v: int32) -> int32:
    return f(v)


# A LAMBDA at the same position reads a frame MEMBER, which has no variable
# form: the capture is an init-capture snapshot of the member, and it stays
# valid across the await without holding the frame.
async def lambda_capture(n: int32) -> int32:
    first = apply(lambda x: x + n, 1)  # tpyc: ok
    await asyncio.sleep(0)
    return first + apply(lambda x: x + n, 2)


async def main() -> None:
    print((await capture_mutate()).n)
    print(await across_await())
    print(await lambda_capture(10))


asyncio.run(main())
