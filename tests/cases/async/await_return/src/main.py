# `return await sub()` -- the await result returns directly without binding.
from tpy.extern import cpp_template
from tpy import Int32

async def sub() -> Int32:
    return Int32(99)

async def caller() -> Int32:
    return await sub()

@cpp_template("std::move(::tpyapp::main::caller().poll(::tpy::Waker{{}})).value()")
def drive_caller() -> Int32: ...

def main() -> None:
    print(drive_caller())

main()
