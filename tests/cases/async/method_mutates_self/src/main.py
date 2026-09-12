# Mutating-self in an async method body. Auto-readonly inference
# detects the assignment to self.count and leaves the method
# non-readonly, so __self is captured as the mutable reference
# `Class&` (not `const Class&`). Exercises the non-const __self
# branch in AsyncCoroCodegen._classify_params.
import asyncio
from tpy import int32


class Counter:
    count: int32

    def __init__(self) -> None:
        self.count = 0

    async def bump(self, by: int32) -> int32:
        self.count += by
        return self.count


async def main_coro() -> None:
    c = Counter()
    print(await c.bump(3))
    print(await c.bump(4))
    print(c.count)


asyncio.run(main_coro())
