# `return` inside a suspending `finally` swallows a raise from the try
# body. Expected: caller returns 42, no exception propagates.
import asyncio


async def cleanup() -> None:
    await asyncio.sleep(0)


async def caller() -> int:
    try:
        raise ValueError("oops")
    finally:
        await cleanup()
        return 42


def main() -> None:
    print(asyncio.run(caller()))


main()
