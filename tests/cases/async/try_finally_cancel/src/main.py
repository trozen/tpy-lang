import asyncio
from tpy.extern import cpp_template
from tpy import Int32
from tpy.coro import Task

async def coro() -> Int32:
    try:
        await asyncio.sleep(60.0)
        return Int32(99)
    finally:
        print("cleanup ran")

@cpp_template("::tpy::Task<int32_t>::from_coro({0})")
def make_task[T](coro: T) -> Task[Int32]: ...

@cpp_template("({0}).poll(::tpy::Waker{{}}).is_pending()")
def poll_pending(t: Task[Int32]) -> bool: ...

@cpp_template("({0}).cancel()")
def cancel_task(t: Task[Int32]) -> None: ...

@cpp_template("::tpy::task_poll_cancelled({0})")
def poll_expecting_cancel(t: Task[Int32]) -> bool: ...

def main() -> None:
    t: Task[Int32] = make_task(coro())
    if poll_pending(t):
        print("first-poll-pending")
    cancel_task(t)
    if poll_expecting_cancel(t):
        print("got-cancelled")

main()
