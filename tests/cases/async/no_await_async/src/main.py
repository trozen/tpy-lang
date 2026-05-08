# Smallest async def: no awaits, single state. Compiles to a struct with
# poll() that sets state to DONE on first call and returns Poll::ready(value).
from tpy.extern import cpp_template
from tpy import Int32

async def f() -> Int32:
    return Int32(42)

# Drive the coroutine inline via cpp_template. Ideally this would use
# `tpy.task.poll_once`, but that's blocked on two compiler gaps:
# (1) `f()` for `async def f() -> T` is currently sema'd as `T`, not as
#     the coroutine struct, so `Awaitable[T]` protocol matching fails.
# (2) Protocol-monomorphization with explicit T leaves the second
#     deducible-param unresolved at the C++ template instantiation.
# Tracked in `docs/ASYNC_PROGRESS.md`.
@cpp_template("std::move(::tpyapp::main::f().poll(::tpy::Waker{{}})).value()")
def drive_f() -> Int32: ...

def main() -> None:
    print(drive_f())

main()
