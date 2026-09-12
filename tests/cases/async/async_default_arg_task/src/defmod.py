import asyncio
from typing import Final
from tpy import int32

# The default names THIS module's Final -- a caller in another module must not
# resolve it in its own scope.
BUMP: Final[int32] = 30


async def scaled(a: int32, b: int32 = BUMP) -> int32:
    await asyncio.sleep(0)
    return a + b
