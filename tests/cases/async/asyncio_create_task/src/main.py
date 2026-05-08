import asyncio
from tpy import Int32
from tpy.coro import Task

async def sub() -> Int32:
    return Int32(42)

async def main_coro() -> None:
    t: Task[Int32] = asyncio.create_task(sub())
    val = await t
    print(val)

def main() -> None:
    asyncio.run(main_coro())

main()
