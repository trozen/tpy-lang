# A bound coroutine driven by hand (`__poll__`) may have started, and a started
# frame holds pointers into itself: moving it into a task is rejected.
import asyncio

from tpy import int32
from tpy.coro import Waker


async def leaf(x: int32) -> int32:
    await asyncio.sleep(0)
    return x + 1


async def main() -> None:
    c = leaf(41)
    c.__poll__(Waker())
    # The subject: create_task would move the started frame into the task.
    t = asyncio.create_task(c)  # tpyc: error(/cannot move coroutine 'c' into argument 'coro': the call on line 16 may have started it/)
    print(await t)


asyncio.run(main())
