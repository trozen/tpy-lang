# print() is a Ctrl-C check point: a pending KeyboardInterrupt (request_interrupt
# marks one without consuming it) is raised once the line is written, in every
# position a print chain renders, and a user file= sink cannot swallow it.
import asyncio
from typing import Callable, Iterator
from _bindings.posix_signal import request_interrupt
from tpy import int32, error_return, ReturnException
from tpy.thread import spawn


def pend() -> None:
    request_interrupt()


# free function: the line is written, then the interrupt raised
def free_function() -> None:
    pend()
    try:
        print("free: line printed")  # tpyc: ok -- the subject: raises after writing
        print("free: not reached")
    except KeyboardInterrupt:
        print("free: caught after the line")


def apply(f: Callable[[], None]) -> None:
    f()


# lambda body: the expression-form print chain ends in the same check
def lambda_body() -> None:
    pend()
    try:
        apply(lambda: print("lambda: line printed"))  # tpyc: ok
        print("lambda: not reached")
    except KeyboardInterrupt:
        print("lambda: caught after the line")


def counting() -> Iterator[int32]:
    try:
        yield 1
        print("generator: line printed")  # tpyc: ok -- raised inside the frame
        yield 2
    finally:
        print("generator: finally ran")


# generator body: raised inside the resumed frame, out of the for loop
def generator_body() -> None:
    try:
        for n in counting():
            print("generator: got", n)
            pend()
    except KeyboardInterrupt:
        print("generator: caught")


class NotFound(Exception, ReturnException):
    pass


@error_return(NotFound)
def lookup(key: int32) -> int32:
    print("error_return: line printed")  # tpyc: ok -- throw-tier, passes the expected channel by
    if key < 0:
        raise NotFound
    return key


# @error_return body: the KeyboardInterrupt passes the expected channel by
def error_return_body() -> None:
    pend()
    try:
        try:
            v = lookup(1)
            print("error_return: got", v)
        except NotFound:
            print("error_return: not found (WRONG)")
    except KeyboardInterrupt:
        print("error_return: caught")


# flush=True: the flush precedes the check
def flushed() -> None:
    pend()
    try:
        print("flush: line printed", flush=True)  # tpyc: ok
        print("flush: not reached")
    except KeyboardInterrupt:
        print("flush: caught after the flush")


class Echo:
    pieces: list[str]

    def __init__(self) -> None:
        self.pieces = []

    def write(self, text: str) -> int32:
        self.pieces.append(text)
        # The print inside write() consumes the pending interrupt and raises
        # out of the enclosing print.
        print("sink: write", len(text))
        return len(text)

    def flush(self) -> None:
        pass


# file= sink whose write() prints: the nested check point's interrupt
# propagates out of the outer print instead of vanishing into the stream state
def sink_nested_print() -> None:
    s = Echo()
    pend()
    try:
        print("sink: piece", file=s)  # tpyc: ok
        print("sink: not reached")
    except KeyboardInterrupt:
        print("sink: caught, pieces:", len(s.pieces))


class Record:
    pieces: list[str]

    def __init__(self) -> None:
        self.pieces = []

    def write(self, text: str) -> int32:
        self.pieces.append(text)
        return len(text)

    def flush(self) -> None:
        pass


# end="" with formatted numbers: every piece reaches write() before the check
# (one write() per streamed token, none for the empty end:
# BUGS.md#print-sink-write-call-pattern)
def sink_tail() -> None:
    r = Record()
    pend()
    try:
        print(5, 6, end="", file=r)  # tpyc: ok
        print("tail: not reached")
    except KeyboardInterrupt:
        print("tail: caught, pieces:", ",".join(r.pieces))


async def printing() -> None:
    pend()
    print("async: line printed, no raise")  # tpyc: ok -- asyncio owns delivery
    await asyncio.sleep(0.05)
    print("async: not reached")


# coroutine: under asyncio.run the print does not raise; the loop cancels the
# root task and asyncio.run raises the KeyboardInterrupt
def async_body() -> None:
    try:
        asyncio.run(printing())
    except KeyboardInterrupt:
        print("async: run raised KeyboardInterrupt")


class Worker:
    def run(self) -> None:
        # Not the interrupt target: this print writes and never raises.
        print("thread: worker line printed")  # tpyc: ok


# worker thread: a print off the main thread leaves the interrupt pending;
# the main thread's join is the check point that raises it
def worker_thread() -> None:
    pend()
    h = spawn(Worker())
    try:
        h.join()
        print("thread: join returned (WRONG)")
    except KeyboardInterrupt:
        print("thread: caught at join")


def main() -> None:
    free_function()
    lambda_body()
    generator_body()
    error_return_body()
    flushed()
    sink_nested_print()
    sink_tail()
    worker_thread()
    async_body()


main()

# module level: the same chain in __tpy_init
pend()
try:
    print("module: line printed")  # tpyc: ok
    print("module: not reached")
except KeyboardInterrupt:
    print("module: caught after the line")
