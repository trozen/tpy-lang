# Two nested suspending `finally` bodies; both run on normal exit in
# inner-then-outer order.
import asyncio


async def sub(label: str) -> None:
    print(label)


async def caller() -> None:
    try:
        try:
            pass
        finally:
            await sub("inner")
    finally:
        await sub("outer")


def main() -> None:
    asyncio.run(caller())


main()
