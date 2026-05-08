# asyncio.run should return the awaited coroutine value, not only drive None coros.
import asyncio


async def compute() -> int:
    await asyncio.sleep(0.001)
    return 42


def main() -> None:
    result = asyncio.run(compute())
    print(result)


main()
