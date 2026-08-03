# Async dunders and a coro method whose bare names all collide with main.py's.
import asyncio
from tpy import Int32


class Gate:
    async def __aenter__(self) -> Int32:
        await asyncio.sleep(0)
        return 5

    async def __aexit__(self, et: None, ev: None, tb: None) -> None:
        await asyncio.sleep(0)


class Ticker:
    n: Int32

    def __init__(self, n: Int32) -> None:
        self.n = n

    def __aiter__(self) -> "Ticker":
        return self

    async def __anext__(self) -> Int32:
        if self.n <= 0:
            raise StopAsyncIteration()
        self.n -= 1
        await asyncio.sleep(0)
        return self.n


class Svc:
    async def fetch(self) -> Int32:
        await asyncio.sleep(0)
        return 3
