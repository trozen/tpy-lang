# A coroutine lent to `poll_once`, which may start it, cannot then be moved
# to another name (the `__poll__` face is async/error_coro_move_after_poll).
import asyncio

from tpy import int32
from tpy.coro import poll_once


async def leaf(x: int32) -> int32:
    await asyncio.sleep(0)
    return x + 1


async def main() -> None:
    c = leaf(41)
    poll_once(c)
    # The subject: `d = c` would move the started frame.
    d = c  # tpyc: error(/cannot move coroutine 'c' into 'd': the call on line \d+ may have started it/)
    print(await d)


asyncio.run(main())
