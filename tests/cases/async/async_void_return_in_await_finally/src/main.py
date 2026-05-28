# Void async def with a bare `return` inside a suspending `finally`.
# Exercises the void branch of `_emit_async_finally_exit` (no slot, only
# the pending flag). Expected: prints "did it".
import asyncio


async def cleanup() -> None:
    await asyncio.sleep(0)


async def caller() -> None:
    try:
        print("try body")
    finally:
        await cleanup()
        print("did it")
        return


def main() -> None:
    asyncio.run(caller())


main()
