# `return await sub()` -- the await result returns directly without binding.
from tpy import Int32
from tpy.coro import poll_once

async def sub() -> Int32:
    return Int32(99)

async def caller() -> Int32:
    return await sub()

def main() -> None:
    print(poll_once(caller()).value())

main()
