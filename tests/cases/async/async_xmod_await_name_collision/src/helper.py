# Coros whose names deliberately collide with main.py's, to prove the emit-order
# edges distinguish a cross-module callee from a same-module one.
import asyncio
from tpy import int32


async def step() -> int32:
    await asyncio.sleep(0)
    return 1


async def other() -> int32:
    await asyncio.sleep(0)
    return 2
