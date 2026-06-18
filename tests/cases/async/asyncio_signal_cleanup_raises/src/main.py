# Regression: when the SIGINT-cancelled root raises a DIFFERENT exception while
# unwinding (here from its finally), asyncio.run must propagate THAT exception,
# not replace it with KeyboardInterrupt -- i.e. the shutdown path must never
# swallow a real cleanup error. Matches CPython.
import asyncio
from signal import raise_signal, SIGINT


async def serve() -> None:
    try:
        raise_signal(SIGINT)
        await asyncio.sleep(10.0)
    finally:
        raise ValueError("cleanup failed")


def main() -> None:
    try:
        asyncio.run(serve())
    except ValueError as e:
        print("caught ValueError: " + str(e))
    except KeyboardInterrupt:
        print("caught KeyboardInterrupt (WRONG)")


main()
