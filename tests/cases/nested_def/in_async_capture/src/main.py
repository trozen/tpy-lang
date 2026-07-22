# Nested defs in an async def are frame members: they capture frame-field
# locals (mutation visible after the call) and stay callable across awaits.
import asyncio
from tpy import Int32, Own


class Box:
    n: Int32

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


async def across_await() -> Int32:
    base = 100

    def scaled(x: Int32) -> Int32:
        return x + base

    first = scaled(1)
    await asyncio.sleep(0)
    return first + scaled(2)


async def main() -> None:
    print((await capture_mutate()).n)
    print(await across_await())


asyncio.run(main())
