import asyncio
from tpy import Int32
from tpy.coro import Task, task_from_coro, task_poll_cancelled, poll_once

async def yield_once() -> Int32:
    await asyncio.sleep(60.0)
    return Int32(99)

def main() -> None:
    t: Task[Int32] = task_from_coro(yield_once())
    if poll_once(t).is_pending():
        print("first-poll-pending")
    t.cancel()
    if task_poll_cancelled(t):
        print("got-cancelled")

main()
