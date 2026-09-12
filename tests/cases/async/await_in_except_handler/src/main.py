# Await inside an except handler body.
import asyncio
from tpy import int32

async def value(n: int32) -> int32:
    return n

async def fail() -> int32:
    raise ValueError("oops")

async def go() -> int32:
    try:
        x = await fail()
        return x
    except ValueError:
        y = await value(int32(123))
        return y

def main() -> None:
    print(asyncio.run(go()))

main()
