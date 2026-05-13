# Nested asyncio.run raises a catchable RuntimeError.
import asyncio


async def inner() -> None:
    print("inner ran -- should not happen")


async def outer() -> None:
    try:
        asyncio.run(inner())
    except RuntimeError as e:
        print("caught:", e)


def main() -> None:
    asyncio.run(outer())


main()
