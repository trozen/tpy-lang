import asyncio

async def hello() -> None:
    print("hello async world")

def main() -> None:
    asyncio.run(hello())

main()
