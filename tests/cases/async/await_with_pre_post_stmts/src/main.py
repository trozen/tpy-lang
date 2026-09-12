# Multi-await with non-async statements interleaved. Validates region
# splitting + hoisted locals across multiple suspensions.
from tpy import int32
from tpy.coro import poll_once

async def sub() -> int32:
    return int32(10)

async def caller() -> int32:
    print("before-1")
    x = await sub()
    print("between-1-2")
    y = await sub()
    z: int32 = x + y + int32(1)
    print("after-2")
    return z

def main() -> None:
    print(poll_once(caller()).value())

main()
