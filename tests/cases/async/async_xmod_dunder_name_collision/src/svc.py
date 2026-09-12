# Async dunders and a coro method whose bare names all collide with main.py's.
import asyncio
from tpy import int32


class Gate:
    async def __aenter__(self) -> int32:
        await asyncio.sleep(0)
        return 5

    async def __aexit__(self, et: None, ev: None, tb: None) -> None:
        await asyncio.sleep(0)


class Ticker:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __aiter__(self) -> "Ticker":
        return self

    async def __anext__(self) -> int32:
        if self.n <= 0:
            raise StopAsyncIteration()
        self.n -= 1
        await asyncio.sleep(0)
        return self.n


class Svc:
    async def fetch(self) -> int32:
        await asyncio.sleep(0)
        return 3
