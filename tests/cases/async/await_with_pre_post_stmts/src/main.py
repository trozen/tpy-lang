# Multi-await with non-async statements interleaved. Validates region
# splitting + hoisted locals across multiple suspensions.
from tpy.extern import cpp_template
from tpy import Int32

async def sub() -> Int32:
    return Int32(10)

async def caller() -> Int32:
    print("before-1")
    x = await sub()
    print("between-1-2")
    y = await sub()
    z: Int32 = x + y + Int32(1)
    print("after-2")
    return z

@cpp_template("std::move(::tpyapp::main::caller().poll(::tpy::Waker{{}})).value()")
def drive_caller() -> Int32: ...

def main() -> None:
    print(drive_caller())

main()
