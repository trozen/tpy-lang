# A Ctrl-C check point inside a body C++ runs under noexcept (__del__,
# __move__, the std::hash wrapper, an abandoned frame's cleanup) defers: the
# body runs to completion and the next check point after it raises.
import asyncio
import sys
import time
from typing import Callable, Iterator
from _bindings import posix_socket
from _bindings.posix_signal import request_interrupt
from tpy import int32, uint64, Own
from tpy.coro import Waker


def pend() -> None:
    request_interrupt()


class Loud:
    tag: str

    def __init__(self, tag: str) -> None:
        self.tag = tag

    def __del__(self) -> None:
        print(self.tag + ": del")  # tpyc: ok -- deferred, not raised


def drop_loud(tag: str) -> None:
    x = Loud(tag)
    pend()


# __del__ that prints: the line is written, the next print raises
def del_print() -> None:
    try:
        drop_loud("del_print")
        print("del_print: after the drop")
        print("del_print: not reached")
    except KeyboardInterrupt:
        print("del_print: caught")


def raise_past_loud() -> None:
    x = Loud("del_unwinding")
    pend()
    raise ValueError("boom")


# __del__ running while a ValueError unwinds: the ValueError still arrives
def del_unwinding() -> None:
    try:
        try:
            raise_past_loud()
        except ValueError:
            print("del_unwinding: ValueError caught")
        print("del_unwinding: not reached")
    except KeyboardInterrupt:
        print("del_unwinding: caught")


class Helped:
    def report(self) -> None:
        print("del_helper: helper line")

    def __del__(self) -> None:
        self.report()  # tpyc: ok -- reaches print through a bodied helper


def drop_helped() -> None:
    h = Helped()
    pend()


# __del__ reaching a check point through a bodied helper
def del_helper() -> None:
    try:
        drop_helped()
        print("del_helper: after the drop")
        print("del_helper: not reached")
    except KeyboardInterrupt:
        print("del_helper: caught")


class Calls:
    def __del__(self) -> None:
        f: Callable[[], None] = lambda: print("del_lambda: lambda line")
        f()  # tpyc: ok -- reaches print through a callable value


def drop_calls() -> None:
    c = Calls()
    pend()


# __del__ calling a lambda bound to a local Callable
def del_lambda() -> None:
    try:
        drop_calls()
        print("del_lambda: after the drop")
        print("del_lambda: not reached")
    except KeyboardInterrupt:
        print("del_lambda: caught")


class Sleepy:
    def __del__(self) -> None:
        t0 = time.monotonic()
        time.sleep(0.02)  # tpyc: ok -- not cut short by the pending Ctrl-C
        print("del_sleep: slept the full time", time.monotonic() - t0 >= 0.02)


def drop_sleepy() -> None:
    s = Sleepy()
    pend()


# __del__ with a sleep: it sleeps the full time, then the next print raises
def del_sleep() -> None:
    try:
        drop_sleepy()
        print("del_sleep: after the drop")
        print("del_sleep: not reached")
    except KeyboardInterrupt:
        print("del_sleep: caught")


class Handle:
    fd: int32
    closes: int32

    def __init__(self) -> None:
        self.fd = -1
        self.closes = 0

    def __del__(self) -> None:
        # inert: a bodyless stub over an int and arithmetic -- no scope
        if self.fd >= 0:
            posix_socket.close(self.fd)  # tpyc: ok
            self.fd = -1
        self.closes = self.closes * 2 + 1


# an inert __del__ gets no deferral scope
def del_inert() -> None:
    h = Handle()
    print("del_inert: fd", h.fd)


class LoudKey:
    k: int32

    def __init__(self, k: int32) -> None:
        self.k = k

    def __eq__(self, other: LoudKey) -> bool:
        return self.k == other.k

    def __hash__(self) -> uint64:
        # A silent check point: libstdc++ versions hash a key once or twice
        # on insert, so a print here would make the output host-dependent.
        time.sleep(0)  # tpyc: ok -- deferred in std::hash
        return uint64(self.k)


# __hash__ reaching a check point, used as a dict key: the dict is built, the
# next print raises
def hash_print() -> None:
    try:
        pend()
        d = {LoudKey(5): 1}
        print("hash_print: dict built", len(d))
        print("hash_print: not reached")
    except KeyboardInterrupt:
        print("hash_print: caught")


class QuietKey:
    k: int32

    def __init__(self, k: int32) -> None:
        self.k = k

    def __eq__(self, other: QuietKey) -> bool:
        return self.k == other.k

    def __hash__(self) -> uint64:
        return hash(self.k) ^ 7  # tpyc: ok -- inert: no scope in the wrapper


# an inert __hash__ gets no deferral scope
def hash_inert() -> None:
    s = {QuietKey(1), QuietKey(2), QuietKey(1)}
    print("hash_inert: size", len(s))


class Mover:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __del__(self) -> None:
        pass

    def __move__(self, other: Own[Mover]) -> None:
        print("move_print: relocating", other.n)  # tpyc: ok -- deferred in the move ctor
        self.n = other.n


# __move__ that prints: the relocation completes, the next print raises
def move_print() -> None:
    try:
        m = Mover(3)
        pend()
        moved = m  # forced last-use move -> runs Mover.__move__
        print("move_print: moved", moved.n)
        print("move_print: not reached")
    except KeyboardInterrupt:
        print("move_print: caught")


def ticking(tag: str) -> Iterator[int32]:
    try:
        yield 1
        yield 2
    finally:
        print(tag + ": finally")  # tpyc: ok -- runs in the abandoned frame's destructor


# abandoned generator: its finally prints from the frame destructor
def gen_abandoned() -> None:
    try:
        for x in ticking("gen_abandoned"):
            pend()
            break
        print("gen_abandoned: after the loop")
        print("gen_abandoned: not reached")
    except KeyboardInterrupt:
        print("gen_abandoned: caught")


class Walker:
    def __del__(self) -> None:
        for x in ticking("nested"):
            break
        print("nested: del done")  # tpyc: ok


def drop_walker() -> None:
    w = Walker()
    pend()


# nested deferral: a __del__ abandons a generator whose finally prints
def nested() -> None:
    try:
        drop_walker()
        print("nested: after the drop")
        print("nested: not reached")
    except KeyboardInterrupt:
        print("nested: caught")


def quiet(n: int32) -> Iterator[int32]:
    count = 0
    try:
        yield n  # tpyc: ok -- inert body: the frame destructor opens no scope
        yield n + 1
    finally:
        count += 1


# an inert generator (arithmetic finally) gets no deferral scope
def gen_inert() -> None:
    total = 0
    for x in quiet(5):
        total += x
        break
    print("gen_inert: total", total)


async def say() -> None:
    print("del_asyncio: coroutine line")  # tpyc: ok -- the run handles no Ctrl-C


class Runner:
    def __del__(self) -> None:
        asyncio.run(say())  # tpyc: ok -- SIGINT ownership declined under the scope


def drop_runner() -> None:
    r = Runner()
    pend()


# asyncio.run inside a __del__: the run completes, the next print raises
def del_asyncio() -> None:
    try:
        drop_runner()
        print("del_asyncio: after the drop")
        print("del_asyncio: not reached")
    except KeyboardInterrupt:
        print("del_asyncio: caught")


class Writer:
    def __del__(self) -> None:
        sys.stdout.write("del_write: written\n")  # tpyc: ok -- a marked method stub on a global receiver


def drop_writer() -> None:
    w = Writer()
    pend()


# __del__ whose only check point is sys.stdout.write
def del_write() -> None:
    try:
        drop_writer()
        print("del_write: after the drop")
        print("del_write: not reached")
    except KeyboardInterrupt:
        print("del_write: caught")


class Gauge:
    def __init__(self) -> None:
        pass

    @property
    def level(self) -> int32:
        print("del_property: getter line")
        return 7


class Reader:
    g: Gauge
    seen: int32

    def __init__(self) -> None:
        self.g = Gauge()
        self.seen = 0

    def __del__(self) -> None:
        self.seen = self.g.level  # tpyc: ok -- the property read is a getter call


def drop_reader() -> None:
    r = Reader()
    pend()


# __del__ reading a property whose getter prints
def del_property() -> None:
    try:
        drop_reader()
        print("del_property: after the drop")
        print("del_property: not reached")
    except KeyboardInterrupt:
        print("del_property: caught")


class Countdown:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __iter__(self) -> Countdown:
        return self

    def __next__(self) -> int32:
        if self.n <= 0:
            raise StopIteration
        print("del_iter: next", self.n)
        self.n -= 1
        return self.n


class Drainer:
    src: Countdown

    def __init__(self) -> None:
        self.src = Countdown(2)

    def __del__(self) -> None:
        for x in self.src:  # tpyc: ok -- a user __next__ that prints
            pass


def drop_drainer() -> None:
    d = Drainer()
    pend()


# __del__ iterating a user iterator whose __next__ prints
def del_iter() -> None:
    try:
        drop_drainer()
        print("del_iter: after the drop")
        print("del_iter: not reached")
    except KeyboardInterrupt:
        print("del_iter: caught")


async def coro_body(tag: str) -> None:
    try:
        await asyncio.sleep(10)
        print(tag + ": not reached")
    finally:
        print(tag + ": finally")  # tpyc: ok -- runs in the abandoned frame's destructor


def drop_started_coro() -> None:
    c = coro_body("coro_abandoned")
    # Driven by hand outside asyncio.run, which would own Ctrl-C delivery:
    # the first poll suspends at the sleep.
    c.__poll__(Waker())
    pend()


# abandoned coroutine frame: its finally prints from the frame destructor
def coro_abandoned() -> None:
    try:
        drop_started_coro()
        print("coro_abandoned: after the drop")
        print("coro_abandoned: not reached")
    except KeyboardInterrupt:
        print("coro_abandoned: caught")


class Exiter:
    def __enter__(self) -> None:
        pass

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        print("gen_with: exit, exceptional", exc_val is not None)


def guarded() -> Iterator[int32]:
    cm = Exiter()
    with cm:
        yield 1  # tpyc: ok -- __exit__ runs in the abandoned frame's destructor
        yield 2


# abandoned generator suspended inside a `with`: __exit__ prints from the
# frame destructor
def gen_with() -> None:
    try:
        for x in guarded():
            pend()
            break
        print("gen_with: after the loop")
        print("gen_with: not reached")
    except KeyboardInterrupt:
        print("gen_with: caught")


class Tally:
    xs: list[int32]
    total: int32
    label: str
    note: str
    flag: int32

    def __init__(self) -> None:
        self.xs = [1, 2, 3]
        self.total = 0
        self.label = "a"
        self.note = ""
        self.flag = 0

    def __del__(self) -> None:
        # inert: every statement is on the allowlist -- no scope
        for x in self.xs:  # tpyc: ok
            self.total += x
        self.label = self.label + "b"
        self.note = f"{self.total}:{self.flag}"
        a, b = 1, 2
        tmp = a + b
        del tmp
        if self.flag > 100:
            raise ValueError("never")  # tpyc: warning(/'raise' in '__del__' cannot propagate/)


# an inert __del__ spanning several allowlist branches gets no deferral scope
def del_inert_mix() -> None:
    t = Tally()
    print("del_inert_mix: total", t.total)


class Handled:
    def __del__(self) -> None:
        try:
            print("del_handler: line printed")
        except KeyboardInterrupt:  # tpyc: warning(/'except KeyboardInterrupt' in '__del__' never runs/)
            print("del_handler: handler ran (WRONG)")


def drop_handled() -> None:
    h = Handled()
    pend()


# a handler inside __del__ never sees the deferred Ctrl-C: it is raised outside
def del_handler() -> None:
    try:
        drop_handled()
        print("del_handler: after the drop")
        print("del_handler: not reached")
    except KeyboardInterrupt:
        print("del_handler: caught outside")


def main() -> None:
    del_handler()
    del_print()
    del_unwinding()
    del_helper()
    del_lambda()
    del_sleep()
    del_inert()
    hash_print()
    hash_inert()
    move_print()
    gen_abandoned()
    nested()
    gen_inert()
    del_asyncio()
    del_write()
    del_property()
    del_iter()
    coro_abandoned()
    gen_with()
    del_inert_mix()


main()

# module level: a global destroyed after the program ends with a Ctrl-C
# pending prints its line; the pending Ctrl-C is dropped and the exit is clean
keeper = Loud("module")
pend()
