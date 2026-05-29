# `return` in the inner TRY body parks into the inner's slot, then
# forwards through both nested suspending `finally` bodies before the
# value is delivered.
import asyncio


async def sub(label: str) -> None:
    print(label)


async def caller() -> int:
    try:
        try:
            return 7
        finally:
            await sub("inner")
    finally:
        await sub("outer")


def main() -> None:
    print(asyncio.run(caller()))


main()
