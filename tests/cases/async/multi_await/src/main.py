# Multi-await with arg passing between awaits. The result of the first
# await flows into the second's call.
from tpy import int32
from tpy.coro import poll_once

async def add_one(x: int32) -> int32:
    return x + int32(1)

async def caller() -> int32:
    a = await add_one(int32(5))
    b = await add_one(a)
    return b

def main() -> None:
    print(poll_once(caller()).value())

main()
