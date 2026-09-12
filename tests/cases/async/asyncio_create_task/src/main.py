import asyncio
from tpy import int32
from asyncio import Task

async def sub() -> int32:
    return int32(42)

async def main_coro() -> None:
    t: Task[int32] = asyncio.create_task(sub())
    val = await t
    print(val)

def main() -> None:
    asyncio.run(main_coro())

main()
