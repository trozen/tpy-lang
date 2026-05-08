import asyncio

async def sub() -> None:
    print("sub")

async def main_coro() -> None:
    try:
        await sub()
        print("after-await")
    finally:
        print("finally")

def main() -> None:
    asyncio.run(main_coro())

main()
