# `await rvalue.method()` is rejected: the coro struct captures the
# receiver as `Class&` for the duration of polling, which would dangle
# when bound to a temporary. User must bind the receiver first.
import asyncio


class Adder:
    base: int

    def __init__(self, b: int) -> None:
        self.base = b

    async def add(self, x: int) -> int:
        return self.base + x


async def main_coro() -> None:
    r = await Adder(10).add(5)  # tpyc: error(/receiver of an awaited async method must be a stable lvalue/)
    print(r)


asyncio.run(main_coro())
