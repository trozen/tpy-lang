# A synchronous SIGINT (signal.raise_signal) raises KeyboardInterrupt on the
# main thread at the raise point, running finally / __exit__ cleanup on the way
# out -- one section per position the exception unwinds through.
import asyncio
import signal
from typing import Iterator


def interrupt() -> None:
    signal.raise_signal(signal.SIGINT)  # tpyc: ok -- raises before returning


# free function: caught in the function that raised it
def free_function() -> None:
    try:
        interrupt()  # tpyc: ok -- the subject: raises KeyboardInterrupt here
        print("free: not reached")
    except KeyboardInterrupt:
        print("free: caught KeyboardInterrupt")


# try/finally: the finally body runs while the interrupt unwinds
def with_finally() -> None:
    try:
        try:
            interrupt()  # tpyc: ok -- unwinds through the finally below
        finally:
            print("finally: cleanup ran")
    except KeyboardInterrupt:
        print("finally: caught after cleanup")


class Guard:
    events: list[str]

    def __init__(self) -> None:
        self.events = []

    def __enter__(self) -> "Guard":
        self.events.append("enter")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.events.append("exit")


# context-manager body: __exit__ runs while the interrupt unwinds
def with_exit() -> None:
    guard = Guard()
    try:
        with guard:
            interrupt()  # tpyc: ok -- unwinds through __exit__
    except KeyboardInterrupt:
        print("with: " + ",".join(guard.events))


class Worker:
    steps: int

    def __init__(self) -> None:
        self.steps = 0

    def run(self) -> None:
        self.steps += 1
        interrupt()  # tpyc: ok -- raised inside a method, caught by its caller
        self.steps += 1


# method: raised in a method body, caught by the caller
def method() -> None:
    w = Worker()
    try:
        w.run()
    except KeyboardInterrupt:
        print("method: caught after " + str(w.steps) + " step")


def numbers() -> Iterator[int]:
    yield 1
    try:
        interrupt()  # tpyc: ok -- raised inside the generator body
        yield 2
    finally:
        print("generator: finally ran")


# generator: raised in the generator body, caught around the for loop
def generator() -> None:
    seen: list[int] = []
    try:
        for n in numbers():
            seen.append(n)
    except KeyboardInterrupt:
        print("generator: caught after " + str(len(seen)) + " item")


# closure: raised in a nested function that mutates a nonlocal first
def closure() -> None:
    count = 0

    def bump() -> None:
        nonlocal count
        count += 1
        interrupt()  # tpyc: ok -- raised inside the closure

    try:
        bump()
    except KeyboardInterrupt:
        print("closure: caught, count=" + str(count))


# except Exception: KeyboardInterrupt is a BaseException and passes through
def not_an_exception() -> None:
    try:
        try:
            interrupt()  # tpyc: ok -- not swallowed by `except Exception`
        except Exception:
            print("exception: WRONG, swallowed by except Exception")
    except KeyboardInterrupt:
        print("exception: passed through except Exception")


# except body: a bare raise re-raises the interrupt
def reraise() -> None:
    try:
        try:
            interrupt()
        except KeyboardInterrupt:
            print("reraise: handling")
            raise  # tpyc: ok -- re-raised from the except body
    except KeyboardInterrupt:
        print("reraise: caught again")


async def quiet() -> int:
    await asyncio.sleep(0)
    return 1


# after asyncio.run: delivery returns to synchronous code once the run ends
def after_async_run() -> None:
    print("async: run returned " + str(asyncio.run(quiet())))
    try:
        interrupt()  # tpyc: ok -- synchronous again after the run
        print("async: not reached")
    except KeyboardInterrupt:
        print("async: caught KeyboardInterrupt after the run")


# message: a Ctrl-C's KeyboardInterrupt, like one built with no argument, has
# an empty str()
def message() -> None:
    try:
        interrupt()
    except KeyboardInterrupt as e:
        print("message: signal " + repr(str(e)))  # tpyc: ok -- the subject
    print("message: bare " + repr(str(KeyboardInterrupt())))
    print("message: given " + repr(str(KeyboardInterrupt("why"))))


def main() -> None:
    free_function()
    with_finally()
    with_exit()
    method()
    generator()
    closure()
    not_an_exception()
    reraise()
    after_async_run()
    message()
    print("done")


main()
