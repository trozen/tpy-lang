# `await` inside a `finally` body (v1.5 M3.3). The finally body
# runs as a CFG region; any in-flight exception from the try body
# lives in a `__finally_exc_<n>` std::exception_ptr frame slot and
# is rethrown after the finally completes.
import asyncio


async def cleanup() -> None:
    print("cleanup")


async def caller() -> int:
    try:
        await cleanup()
    finally:
        await cleanup()
    return 7


def main() -> None:
    print(asyncio.run(caller()))


main()
