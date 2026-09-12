# `return await sub()` -- the await result returns directly without binding.
from tpy import int32
from tpy.coro import poll_once

async def sub() -> int32:
    return int32(99)

async def caller() -> int32:
    return await sub()

def main() -> None:
    print(poll_once(caller()).value())

main()
