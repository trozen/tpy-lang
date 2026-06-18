# A bytes parameter of an async def is captured owned in the coro frame, so it
# survives a suspension even when the argument is a temporary.
import asyncio


async def consume(data: bytes) -> int:
    await asyncio.sleep(0.0)
    return len(data)


async def head(data: bytes) -> int:
    await asyncio.sleep(0.0)
    return data[0]


async def main() -> None:
    print(await consume(b"hello"))
    print(await head(b"ABC"))


asyncio.run(main())
