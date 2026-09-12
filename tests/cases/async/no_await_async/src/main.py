# Smallest async def: no awaits, single state. Compiles to a struct with
# poll() that sets state to DONE on first call and returns Poll::ready(value).
from tpy import int32
from tpy.coro import poll_once

async def f() -> int32:
    return int32(42)

def main() -> None:
    print(poll_once(f()).value())

main()
