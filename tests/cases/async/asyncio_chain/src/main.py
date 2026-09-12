import asyncio
from tpy import int32

async def add(x: int32, y: int32) -> int32:
    return x + y

async def compute() -> int32:
    a = await add(int32(3), int32(4))
    b = await add(a, int32(10))
    return b

async def main() -> None:
    result = await compute()
    print(result)

def entry() -> None:
    asyncio.run(main())

entry()
