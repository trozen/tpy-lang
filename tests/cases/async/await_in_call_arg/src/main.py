import asyncio
from tpy import Int32

async def get_val() -> Int32:
    return Int32(7)

async def main_coro() -> None:
    print(await get_val())  # await in argument position
    print(await get_val() + Int32(1))  # await mixed with binop

def main() -> None:
    asyncio.run(main_coro())

main()
