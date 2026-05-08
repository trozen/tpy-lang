import asyncio
from tpy import Int32
from tpy.coro import Task, task_from_coro, task_poll_cancelled, poll_once

async def coro() -> Int32:
    try:
        await asyncio.sleep(60.0)
        return Int32(99)
    finally:
        print("cleanup ran")

def main() -> None:
    t: Task[Int32] = task_from_coro(coro())
    if poll_once(t).is_pending():
        print("first-poll-pending")
    t.cancel()
    if task_poll_cancelled(t):
        print("got-cancelled")

main()
