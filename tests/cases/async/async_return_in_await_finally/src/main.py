# `return` inside a suspending `finally` overrides a return in the try
# body (Python: return-in-finally wins). Expected: caller returns 2.
import asyncio


async def cleanup() -> None:
    await asyncio.sleep(0)


async def caller() -> int:
    try:
        return 1
    finally:
        await cleanup()
        return 2


def main() -> None:
    print(asyncio.run(caller()))


main()
