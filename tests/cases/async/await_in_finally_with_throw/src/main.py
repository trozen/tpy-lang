# `await` in finally with a throw from the try body. The catch in
# the try-body case saves the exception via std::current_exception()
# to a frame field; the finally body suspends on cleanup, then
# AsyncFinallyExit rethrows the saved exception. An outer except
# handler catches it.
import asyncio


async def boom() -> int:
    raise ValueError("inside boom")


async def cleanup() -> None:
    print("cleanup")


async def caller() -> int:
    try:
        try:
            x = await boom()
        finally:
            await cleanup()
    except ValueError:
        print("caught")
        return 42
    return 0


def main() -> None:
    print(asyncio.run(caller()))


main()
