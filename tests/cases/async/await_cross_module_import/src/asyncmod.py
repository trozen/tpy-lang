import asyncio
from tpy import Int32

async def ping() -> Int32:
    await asyncio.sleep(0)
    return 42

async def add(a: Int32, b: Int32) -> Int32:
    await asyncio.sleep(0)
    return a + b
