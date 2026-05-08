import asyncio
from tpy import Int32

async def add(x: Int32, y: Int32) -> Int32:
    return x + y

async def compute() -> Int32:
    a = await add(Int32(3), Int32(4))
    b = await add(a, Int32(10))
    return b

async def main() -> None:
    result = await compute()
    print(result)

def entry() -> None:
    asyncio.run(main())

entry()
