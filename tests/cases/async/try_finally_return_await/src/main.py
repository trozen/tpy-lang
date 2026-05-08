import asyncio
from tpy import Int32

async def sub() -> Int32:
    return Int32(42)

async def main_coro() -> Int32:
    try:
        return await sub()
    finally:
        print("finally-ran")

def main() -> None:
    asyncio.run(main_coro())

main()
