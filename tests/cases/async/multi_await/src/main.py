# Multi-await with arg passing between awaits. The result of the first
# await flows into the second's call.
from tpy import Int32
from tpy.coro import poll_once

async def add_one(x: Int32) -> Int32:
    return x + Int32(1)

async def caller() -> Int32:
    a = await add_one(Int32(5))
    b = await add_one(a)
    return b

def main() -> None:
    print(poll_once(caller()).value())

main()
