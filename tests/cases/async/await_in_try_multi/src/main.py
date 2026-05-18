# Multiple awaits inside a single try body.
import asyncio
from tpy import Int32

async def value(n: Int32) -> Int32:
    return n

async def go() -> Int32:
    try:
        a = await value(Int32(10))
        b = await value(Int32(20))
        c = await value(Int32(30))
        return a + b + c
    except ValueError:
        return Int32(-1)

def main() -> None:
    print(asyncio.run(go()))

main()
