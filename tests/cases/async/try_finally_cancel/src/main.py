import asyncio
from tpy import int32
from asyncio import Task, task_from_coro
from tpy.coro import task_poll_cancelled, poll_once

async def coro() -> int32:
    try:
        await asyncio.sleep(60.0)
        return int32(99)
    finally:
        print("cleanup ran")

def main() -> None:
    t: Task[int32] = task_from_coro(coro())
    if poll_once(t).is_pending():
        print("first-poll-pending")
    t.cancel()
    if task_poll_cancelled(t):
        print("got-cancelled")

main()
