import asyncio
from tpy import int32

async def get_val() -> int32:
    return int32(7)

async def main_coro() -> None:
    print(await get_val())  # await in argument position
    print(await get_val() + int32(1))  # await mixed with binop

def main() -> None:
    asyncio.run(main_coro())

main()
