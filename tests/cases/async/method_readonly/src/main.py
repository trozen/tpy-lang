# Auto-readonly inference on an async method -- the body doesn't
# mutate self, so the method is inferred @readonly and __self is
# captured as `const Class&`.
import asyncio


class Reporter:
    value: int

    def __init__(self, v: int) -> None:
        self.value = v

    async def describe(self) -> int:
        return self.value + 100


async def main_coro() -> None:
    r = Reporter(42)
    print(await r.describe())


asyncio.run(main_coro())
