# SIGINT while the root keeps itself runnable (an `await asyncio.sleep(0)`
# loop): the run loop sees the signal without blocking and cancels the root.
# A second SIGINT raised in the cleanup, before it blocks in a long sleep,
# must still wake that wait (the wake fd is re-armed after every collect) and
# escapes as KeyboardInterrupt. Matches CPython's asyncio.run.
import asyncio
import time
from signal import raise_signal, SIGINT


async def spin() -> None:
    try:
        print("spinning")
        raise_signal(SIGINT)
        while True:
            await asyncio.sleep(0)  # tpyc: ok -- the cancel lands at this suspension
    finally:
        print("cleanup")
        raise_signal(SIGINT)
        await asyncio.sleep(10.0)  # tpyc: ok -- the second Ctrl-C ends the run here
        print("not reached")


def main() -> None:
    start = time.monotonic()
    try:
        asyncio.run(spin())
        print("run returned normally")
    except KeyboardInterrupt:
        print("caught KeyboardInterrupt")
    # A wake fd left disarmed would sit out the whole 10 s sleep instead.
    print("woke promptly:", time.monotonic() - start < 5.0)


main()
