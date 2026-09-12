import asyncio
from tpy import int32

async def deep() -> int32:
    await asyncio.sleep(0)
    return 5
