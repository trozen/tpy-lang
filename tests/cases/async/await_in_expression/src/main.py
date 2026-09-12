import asyncio
from tpy import int32

async def add_one(x: int32) -> int32:
    return x + int32(1)

async def caller() -> int32:
    # Two awaits in one expression -- requires the await-lift pass.
    return await add_one(int32(5)) + await add_one(int32(10))

async def main_coro() -> None:
    val = await caller()
    print(val)

def main() -> None:
    asyncio.run(main_coro())

main()
