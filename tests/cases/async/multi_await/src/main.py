# Multi-await with arg passing between awaits. The result of the first
# await flows into the second's call.
from tpy.extern import cpp_template
from tpy import Int32

async def add_one(x: Int32) -> Int32:
    return x + Int32(1)

async def caller() -> Int32:
    a = await add_one(Int32(5))
    b = await add_one(a)
    return b

@cpp_template("std::move(::tpyapp::main::caller().poll(::tpy::Waker{{}})).value()")
def drive_caller() -> Int32: ...

def main() -> None:
    print(drive_caller())

main()
