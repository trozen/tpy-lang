# M3.3.2: `return` inside the try body of a try-with-finally-with-
# await. The return value is parked in a `__finally_ret_<n>` frame
# slot + `__finally_pending_<n>` flag; the finally body runs first
# (cleanup), then AsyncFinallyExit emits the deferred Poll::ready.
import asyncio


async def value(n: int) -> int:
    return n


async def cleanup() -> None:
    print("cleanup")


async def caller() -> int:
    try:
        x = await value(42)
        return x
    finally:
        await cleanup()


def main() -> None:
    print(asyncio.run(caller()))


main()
