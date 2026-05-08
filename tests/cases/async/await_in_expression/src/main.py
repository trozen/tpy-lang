import asyncio
from tpy import Int32

async def add_one(x: Int32) -> Int32:
    return x + Int32(1)

async def caller() -> Int32:
    # Two awaits in one expression -- requires the await-lift pass.
    return await add_one(Int32(5)) + await add_one(Int32(10))

async def main_coro() -> None:
    val = await caller()
    print(val)

def main() -> None:
    asyncio.run(main_coro())

main()
