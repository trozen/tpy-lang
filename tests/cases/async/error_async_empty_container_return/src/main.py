# The adjacent coroutine return shape that must keep rejecting: the Own[
# container] slot's value rides a position-blind tail with no target type, so
# an EMPTY container literal would emit a bare `= {}` with no element spelling.
import asyncio

from tpy import Int32, Own


# The reject is reported on the `def` line, not the `return`: a resumable's
# terminator rejects carry no location and fall back to the enclosing
# callable's.
async def empty() -> Own[list[Int32]]:  # tpyc: error(/not yet supported/)
    return []


async def drive() -> None:
    print(len(await empty()))


asyncio.run(drive())
