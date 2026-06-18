# Regression: when the SIGINT-cancelled root coroutine CATCHES its
# CancelledError and returns a value, asyncio.run must return that value -- not
# raise KeyboardInterrupt. (CPython's asyncio.run cancels the root on Ctrl-C; a
# root that suppresses the cancel and returns normally yields its value.)
import asyncio
from asyncio import CancelledError
from signal import raise_signal, SIGINT


async def serve() -> int:
    try:
        raise_signal(SIGINT)
        await asyncio.sleep(10.0)
        return 1
    except CancelledError:
        return 42


def main() -> None:
    print(asyncio.run(serve()))


main()
