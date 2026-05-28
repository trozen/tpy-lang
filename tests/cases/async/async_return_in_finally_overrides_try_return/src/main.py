# Try-body returns one value, finally-body returns another -- finally
# wins (Python). Expected: caller returns "from_finally".
import asyncio


async def cleanup() -> None:
    await asyncio.sleep(0)


async def caller() -> str:
    try:
        return "from_try"
    finally:
        await cleanup()
        return "from_finally"


def main() -> None:
    print(asyncio.run(caller()))


main()
