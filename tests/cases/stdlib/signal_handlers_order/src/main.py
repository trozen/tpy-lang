# Several signals pending at one check point run in ascending order, once
# each; TPy-only, as request_signal pends a signal without sending it (and
# handler_kind reads the runtime's SIGINT kind around asyncio.run).
import asyncio
import signal
from signal import default_int_handler
from types import FrameType

from tpy import int32
from _bindings.posix_signal import handler_kind, request_signal


class Stop(Exception):
    pass


def show(signum: int, frame: FrameType | None) -> None:
    if signum == signal.SIGHUP:
        print("  ran SIGHUP")
    elif signum == signal.SIGUSR1:
        print("  ran SIGUSR1")
    elif signum == signal.SIGUSR2:
        print("  ran SIGUSR2")
    else:
        print("  ran SIGWINCH")


def stops(signum: int, frame: FrameType | None) -> None:
    print("  ran SIGTERM, raising")
    raise Stop()


# ascending order, coalesced: one check point runs each pending handler once
def order() -> None:
    request_signal(signal.SIGUSR2)
    request_signal(signal.SIGUSR1)
    request_signal(signal.SIGUSR1)
    request_signal(signal.SIGHUP)
    print("order: one check point")  # tpyc: ok -- runs the three handlers after the line


# a raising handler stops the delivery; the rest run at the next check point
def raising_first() -> None:
    signal.signal(signal.SIGTERM, stops)
    request_signal(signal.SIGWINCH)
    request_signal(signal.SIGTERM)
    request_signal(signal.SIGHUP)
    try:
        print("raising: check point")  # tpyc: ok -- SIGHUP, then SIGTERM raises
    except Stop:
        print("raising: caught Stop")  # tpyc: ok -- the next check point: SIGWINCH runs
    print("raising: done")


class Dropper:
    def __del__(self) -> None:
        signal.raise_signal(signal.SIGTERM)
        print("del: body finished")


# a signal sent inside __del__ waits for the first check point after it
# (CPython runs the handler inside __del__; declared in LANGUAGE_FEATURES)
def deferred_past_del() -> None:
    try:
        d = Dropper()
        del d  # tpyc: ok -- __del__ defers delivery: a throw there would terminate
        print("del: after the drop")
    except Stop:
        print("del: caught Stop after the __del__")


# a signal with no handler of the layer's is dropped
def no_handler() -> None:
    request_signal(signal.SIGCONT)
    print("no handler: nothing ran")


async def mixed() -> int32:
    try:
        request_signal(signal.SIGINT)
        signal.raise_signal(signal.SIGUSR1)  # tpyc: ok -- runs asyncio.run's SIGINT handler, then SIGUSR1's
        print("mixed: after raise_signal")
        await asyncio.sleep(10.0)
        print("mixed: not reached")
    except asyncio.CancelledError:
        print("mixed: root cancelled")
        raise
    return 1


# asyncio.run: one check point with a Ctrl-C and another signal pending runs
# both in ascending order, the Ctrl-C through asyncio.run's own handler, which
# cancels the root at its next await
def mixed_in_asyncio() -> None:
    try:
        r = asyncio.run(mixed())
        print("mixed: result (WRONG)", r)
    except KeyboardInterrupt:
        print("mixed: KeyboardInterrupt out of the run")


async def reads_kind() -> int32:
    # 2, a user kind: asyncio.run's own handler
    print("restore: SIGINT kind in the run", handler_kind(signal.SIGINT))
    await asyncio.sleep(0.01)
    return 1


async def sets_sigint() -> int32:
    signal.signal(signal.SIGINT, show)
    await asyncio.sleep(0.01)
    return 1


# asyncio.run puts SIGINT back to default_int_handler (kind 1) at its end only
# while its own handler is still the installed one
def restores_own_only() -> None:
    asyncio.run(reads_kind())
    print("restore: after a run", handler_kind(signal.SIGINT))  # tpyc: ok -- 1 again
    asyncio.run(sets_sigint())
    print("restore: after a run that set one", handler_kind(signal.SIGINT))  # tpyc: ok -- 2, the user's
    signal.signal(signal.SIGINT, default_int_handler)


def main() -> None:
    for sig in [signal.SIGHUP, signal.SIGUSR1, signal.SIGUSR2, signal.SIGWINCH]:
        signal.signal(sig, show)
    order()
    raising_first()
    deferred_past_del()
    no_handler()
    mixed_in_asyncio()
    restores_own_only()


main()
