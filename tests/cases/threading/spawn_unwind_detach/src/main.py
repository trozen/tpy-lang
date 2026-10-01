# An unjoined JoinHandle dropped while an exception is propagating detaches its
# thread (dropping always detaches), so the exception reaches its handler.
import time
from typing import Iterator
from tpy import int32
from tpy.thread import spawn


class Slow:
    def run(self) -> int32:
        time.sleep(0.05)
        return 7


def leaves_handle() -> None:
    h = spawn(Slow())
    raise ValueError("boom")  # tpyc: ok -- the subject: unwinds past h
    h.detach()


class Owner:
    def fail(self) -> None:
        h = spawn(Slow())
        raise ValueError("method boom")  # tpyc: ok -- unwinds past h
        h.detach()


class Cleanup:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    def __del__(self) -> None:
        # Dropped by the normal exit of a __del__ that runs during unwinding:
        # an exception is in flight, so this detaches too (known limitation).
        h = spawn(Slow())
        self.n = 1


def fails_with_cleanup() -> None:
    c = Cleanup()
    raise ValueError("del boom")  # tpyc: ok -- unwinding runs Cleanup.__del__


def holds_handle() -> Iterator[int32]:
    h = spawn(Slow())
    yield 1
    yield 2
    h.detach()


def consume_and_raise() -> None:
    for x in holds_handle():
        # The generator frame, suspended while holding h, is dropped by this
        # raise.
        raise ValueError("gen boom " + str(x))  # tpyc: ok


def main() -> None:
    # free function: the handle is a local of the unwound frame
    try:
        leaves_handle()
    except ValueError as e:
        print("free: caught " + str(e))
    # method: same, from a method body
    try:
        Owner().fail()
    except ValueError as e:
        print("method: caught " + str(e))
    # __del__ during unwinding: a handle created and dropped inside it
    try:
        fails_with_cleanup()
    except ValueError as e:
        print("del: caught " + str(e))
    # generator: the handle lives in a suspended generator frame
    try:
        consume_and_raise()
    except ValueError as e:
        print("generator: caught " + str(e))


main()
