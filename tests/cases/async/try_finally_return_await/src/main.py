import asyncio
from tpy import int32

async def sub() -> int32:
    return int32(42)

async def main_coro() -> int32:
    try:
        return await sub()
    finally:
        print("finally-ran")

def main() -> None:
    asyncio.run(main_coro())

main()
