import asyncio
from tpy import Int32

async def deep() -> Int32:
    await asyncio.sleep(0)
    return 5
