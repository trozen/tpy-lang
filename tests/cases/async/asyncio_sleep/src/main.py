import asyncio

async def main_coro() -> None:
    print("first")
    await asyncio.sleep(0.05)
    print("second")
    await asyncio.sleep(0.05)
    print("third")

def main() -> None:
    asyncio.run(main_coro())

main()
