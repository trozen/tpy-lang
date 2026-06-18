# asyncio.run installs a SIGINT handler: a signal (here delivered to ourselves
# via raise_signal) cancels the root task so its finally cleanup runs, then
# asyncio.run raises KeyboardInterrupt -- matching CPython's asyncio.run. The
# signal is raised right before an await so the executor regains control and
# delivers the cancellation at the suspension point.
import asyncio
from signal import raise_signal, SIGINT


async def serve() -> None:
    try:
        print("serving")
        raise_signal(SIGINT)
        await asyncio.sleep(10.0)
        print("not reached")
    finally:
        print("shutdown cleanup")


def main() -> None:
    try:
        asyncio.run(serve())
        print("run returned normally")
    except KeyboardInterrupt:
        print("caught KeyboardInterrupt")


main()
