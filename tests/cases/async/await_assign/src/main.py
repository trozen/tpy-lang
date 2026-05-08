# Single statically-resolved await bound to a local. Polled via inline
# C++ that constructs a default Waker (no executor in v1).
#
# Ideally this would use `tpy.task.poll_once`, but that's blocked on two
# compiler gaps (sema returns the awaited type for async-def calls;
# protocol-monomorphization with explicit T). Tracked in
# `docs/ASYNC_PROGRESS.md`.
from tpy.extern import cpp_template
from tpy import Int32

async def sub() -> Int32:
    return Int32(42)

async def caller() -> Int32:
    x = await sub()
    return x

@cpp_template("std::move(::tpyapp::main::caller().poll(::tpy::Waker{{}})).value()")
def drive_caller() -> Int32: ...

def main() -> None:
    print(drive_caller())

main()
