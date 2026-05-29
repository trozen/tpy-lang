# `return` inside the inner suspending `finally` is forwarded through
# the outer suspending `finally` before the value is delivered.
import asyncio


async def sub(label: str) -> None:
    print(label)


async def caller() -> int:
    try:
        try:
            pass
        finally:
            await sub("inner")
            return 42
    finally:
        await sub("outer")


def main() -> None:
    print(asyncio.run(caller()))


main()
