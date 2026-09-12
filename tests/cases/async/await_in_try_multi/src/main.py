# Multiple awaits inside a single try body.
import asyncio
from tpy import int32

async def value(n: int32) -> int32:
    return n

async def go() -> int32:
    try:
        a = await value(int32(10))
        b = await value(int32(20))
        c = await value(int32(30))
        return a + b + c
    except ValueError:
        return int32(-1)

def main() -> None:
    print(asyncio.run(go()))

main()
