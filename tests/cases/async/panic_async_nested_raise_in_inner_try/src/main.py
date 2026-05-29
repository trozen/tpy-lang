# Raise in the innermost try body; both nested suspending `finally`
# bodies run before the unhandled exception propagates.
import asyncio


async def sub(label: str) -> None:
    print(label)


async def caller() -> None:
    try:
        try:
            raise ValueError("oops")
        finally:
            await sub("inner")
    finally:
        await sub("outer")


def main() -> None:
    asyncio.run(caller())


main()
