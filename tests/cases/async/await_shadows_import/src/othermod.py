import asyncio
from tpy import int32

async def work() -> int32:
    await asyncio.sleep(0)
    return 99
