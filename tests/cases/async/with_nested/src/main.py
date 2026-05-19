# Nested async-with via a helper async def: each `async with` lives in
# its own CFG, so the inner's TryRegion doesn't collide with the
# outer's. (Direct nesting `async with X: async with Y:` in the same
# function hits the same M3.3 "two `await`-in-`finally` regions"
# restriction -- tracked separately.)
import asyncio


class CM:
    name: str

    def __init__(self, n: str) -> None:
        self.name = n

    async def __aenter__(self) -> str:
        print(f"aenter {self.name}")
        return self.name

    async def __aexit__(self, exc_type: None, exc_val: None, exc_tb: None) -> None:
        print(f"aexit {self.name}")


async def inner() -> None:
    async with CM("inner") as b:
        print(f"inner body {b}")


async def main_coro() -> None:
    async with CM("outer") as a:
        print(f"outer body {a}")
        await inner()


asyncio.run(main_coro())
