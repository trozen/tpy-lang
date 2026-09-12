import asyncio
from tpy import int32

async def ping() -> int32:
    await asyncio.sleep(0)
    return 42

async def add(a: int32, b: int32) -> int32:
    await asyncio.sleep(0)
    return a + b
