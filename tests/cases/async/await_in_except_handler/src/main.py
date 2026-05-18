# Await inside an except handler body.
import asyncio
from tpy import Int32

async def value(n: Int32) -> Int32:
    return n

async def fail() -> Int32:
    raise ValueError("oops")

async def go() -> Int32:
    try:
        x = await fail()
        return x
    except ValueError:
        y = await value(Int32(123))
        return y

def main() -> None:
    print(asyncio.run(go()))

main()
