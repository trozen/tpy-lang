# Single statically-resolved await bound to a local. Polled via
# `poll_once` (no executor in v1).
from tpy import Int32
from tpy.coro import poll_once

async def sub() -> Int32:
    return Int32(42)

async def caller() -> Int32:
    x = await sub()
    return x

def main() -> None:
    print(poll_once(caller()).value())

main()
