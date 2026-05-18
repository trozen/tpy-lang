# M3.3.1 + M3.3.2 combined: `return` inside an except handler of
# a try-with-finally-with-await. Try raises, handler returns the
# replacement value into the pending-return slot, finally runs,
# then the deferred Poll::ready fires.
import asyncio


async def boom() -> int:
    raise ValueError("inside boom")


async def cleanup() -> None:
    print("cleanup")


async def caller() -> int:
    try:
        return await boom()
    except ValueError:
        return 99
    finally:
        await cleanup()


def main() -> None:
    print(asyncio.run(caller()))


main()
