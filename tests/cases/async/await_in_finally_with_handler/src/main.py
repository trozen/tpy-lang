# M3.3.1: `await` inside `finally` combined with `except` handlers.
# The try body raises -> handler runs -> finally runs -> control
# continues past the try.
import asyncio


async def cleanup() -> None:
    print("cleanup")


async def caller() -> int:
    try:
        raise ValueError("boom")
    except ValueError:
        print("caught")
    finally:
        await cleanup()
    return 7


def main() -> None:
    print(asyncio.run(caller()))


main()
