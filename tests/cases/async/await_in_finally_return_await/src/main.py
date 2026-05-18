# Regression: `return await X` inside a try body whose finally has
# an await (M3.3.2). The AwaitKind.RETURN resume path must route
# through the pending-return slot + flag and let AsyncFinallyExit
# emit the deferred Poll::ready -- not emit Poll::ready directly
# (which would bypass the finally body).
import asyncio


async def value(n: int) -> int:
    return n


async def cleanup() -> None:
    print("cleanup")


async def caller() -> int:
    try:
        return await value(42)
    finally:
        await cleanup()


def main() -> None:
    print(asyncio.run(caller()))


main()
