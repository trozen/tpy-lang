import asyncio
from typing import Final
from tpy import Int32

# The default names THIS module's Final -- a caller in another module must not
# resolve it in its own scope.
BUMP: Final[Int32] = 30


async def scaled(a: Int32, b: Int32 = BUMP) -> Int32:
    await asyncio.sleep(0)
    return a + b
