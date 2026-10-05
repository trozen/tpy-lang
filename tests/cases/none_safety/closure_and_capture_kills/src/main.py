# What a control-flow meet (loop back edge, handler / finally entry) and a call
# kill: handler and match capture names, closures that rebind or write captures.
import asyncio
from typing import Iterator

from tpy import Fn, readonly


class M:
    v: int | None

    def __init__(self) -> None:
        self.v = 1


class N:
    v: int | None
    inner: M

    def __init__(self) -> None:
        self.v = 1
        self.inner = M()

    def reset(self) -> bool:
        self.v = None
        return True

    @readonly
    def describe(self) -> str:
        return "N"

    # method: the closure writes through `self`
    def method_capture(self) -> None:
        def wipe() -> None:
            self.v = None

        if self.v is not None:
            wipe()
            y = self.v  # tpyc: type(/None/)
            print("method_capture:", y)


# match capture in a loop: `case x` rebinds the narrowed local on the next pass
def capture_loop(v: int | None) -> None:
    x: int | None = 5
    if x is not None:
        i = 0
        while i < 2:
            y = x  # tpyc: type(/None/)
            print("capture_loop:", y)
            match v:
                case x:
                    pass
            i += 1


# match capture of another name: the narrowing of x survives the loop
def capture_other(v: int | None) -> None:
    x: int | None = 5
    if x is not None:
        i = 0
        while i < 2:
            y = x  # tpyc: type(int)
            print("capture_other:", y + 1)
            match v:
                case z:
                    pass
            i += 1


# handler `as` name in a loop: pins only the static kill of err's narrowing
# at the loop-entry meet (the handler rebinds the name). The handler runs
# after the last read: TPy's catch binds a fresh reference instead of
# rebinding the outer err, and CPython unbinds err when the handler exits,
# so a later read would diverge (BUGS.md#handler-name-not-unbound).
def handler_as() -> None:
    err: ValueError | None = ValueError("a")
    if err is not None:
        i = 0
        while i < 2:
            y = err  # tpyc: type(/None/)
            print("handler_as:", y)
            if i == 1:
                try:
                    raise ValueError("b")
                except ValueError as err:
                    pass
            i += 1


def boom() -> None:
    raise ValueError("boom")


# closure defined before the loop: a call in the body may run it, so the back edge kills x
def closure_loop() -> None:
    x: int | None = 5

    def clear() -> None:
        nonlocal x
        x = None

    if x is not None:
        i = 0
        while i < 2:
            y = x  # tpyc: type(/None/)
            print("closure_loop:", y)
            clear()
            i += 1


# closure defined before the loop, no call in the body: x stays narrowed
def closure_loop_uncalled() -> None:
    x: int | None = 5

    def clear() -> None:
        nonlocal x
        x = None

    total = 0
    if x is not None:
        i = 0
        while i < 2:
            y = x  # tpyc: type(int)
            total += y
            i += 1
    print("closure_loop_uncalled:", total)
    clear()


# closure defined before the try: the handler is entered after the try body's call ran it
def closure_handler() -> None:
    x: int | None = 5

    def clear() -> None:
        nonlocal x
        x = None

    if x is not None:
        try:
            clear()
            boom()
        except ValueError:
            y = x  # tpyc: type(/None/)
            print("closure_handler:", y)


# closure defined before the try: the finally is entered after the try body's call ran it
def closure_finally() -> None:
    x: int | None = 5

    def clear() -> None:
        nonlocal x
        x = None

    if x is not None:
        try:
            clear()
        finally:
            y = x  # tpyc: type(/None/)
            print("closure_finally:", y)


# closure field store, straight line: the call runs it
def field_straight() -> None:
    t = N()

    def wipe() -> None:
        t.v = None

    if t.v is not None:
        wipe()
        y = t.v  # tpyc: type(/None/)
        print("field_straight:", y)


# closure field store defined before the loop: the call in the body reaches the back edge
def field_loop() -> None:
    t = N()

    def wipe() -> None:
        t.v = None

    if t.v is not None:
        i = 0
        while i < 2:
            y = t.v  # tpyc: type(/None/)
            print("field_loop:", y)
            wipe()
            i += 1


# closure storing through ANOTHER captured record: t.v stays narrowed
def field_loop_other() -> None:
    t = N()
    u = N()

    def wipe() -> None:
        u.v = None

    if t.v is not None:
        i = 0
        while i < 2:
            y = t.v  # tpyc: type(int)
            print("field_loop_other:", y + 1)
            wipe()
            i += 1


# closure field store run by the try body: the handler is entered after it
def field_handler() -> None:
    t = N()

    def wipe() -> None:
        t.v = None

    if t.v is not None:
        try:
            wipe()
            boom()
        except ValueError:
            y = t.v  # tpyc: type(/None/)
            print("field_handler:", y)


# closure field store called in the narrowing condition itself
def field_condition() -> None:
    t = N()

    def wipe() -> bool:
        t.v = None
        return True

    if t.v is not None and wipe():
        y = t.v  # tpyc: type(/None/)
        print("field_condition:", y)


# closure receiver: an append through the capture kills the len-derived range of i
def receiver_len(xs: list[int]) -> None:
    def grow() -> None:
        xs.append(9)

    for i in range(len(xs)):
        grow()
        print("receiver_len:", xs[i])  # tpyc: bounds_checked(xs)


# closure-local alias of the capture: u = t; u.v = None writes t.v
def local_alias() -> None:
    t = N()

    def wipe() -> None:
        u = t
        u.v = None

    if t.v is not None:
        wipe()
        y = t.v  # tpyc: type(/None/)
        print("local_alias:", y)


# projection: u = t.inner; u.v = None writes t.inner.v and leaves t.v
def projection() -> None:
    t = N()

    def wipe() -> None:
        u = t.inner
        u.v = None

    if t.v is not None and t.inner.v is not None:
        wipe()
        y = t.inner.v  # tpyc: type(/None/)
        z = t.v  # tpyc: type(int)
        print("projection:", y, z + 1)


# a closure parameter shadowing the outer name writes its argument, not the outer t
def param_shadow() -> None:
    t = N()
    other = N()

    def wipe(t: N) -> None:
        t.v = None

    if t.v is not None:
        wipe(other)
        y = t.v  # tpyc: type(int)
        print("param_shadow:", y + 1)


def call_fn(f: Fn[[], bool]) -> bool:
    return f()


# a lambda created in the closure writes through an alias of the capture
def lambda_in_def() -> None:
    t = N()

    def wipe() -> None:
        # The lambda reads a def-local alias: one reading the capture t
        # itself does not build (BUGS.md#nested-def-lambda-capture-missing).
        u = t
        done = call_fn(lambda: u.reset())
        print("lambda_in_def wiped:", done)

    if t.v is not None:
        wipe()
        y = t.v  # tpyc: type(/None/)
        print("lambda_in_def:", y)


# generator body: the closure runs between the narrowing and the read
def gen_capture() -> Iterator[int]:
    t = N()

    def wipe() -> None:
        t.v = None

    if t.v is not None:
        wipe()
        y = t.v  # tpyc: type(/None/)
        print("gen_capture:", y)
        yield 0


class O:
    def __init__(self, name: str | None) -> None:
        self.name = name


def clear_name(o: O) -> None:
    o.name = None


class Scope:
    def __enter__(self) -> "Scope":
        return self

    def __exit__(self, et, ev, tb) -> None:
        pass


# handler `as` name of ANOTHER local: the narrowing of err survives the loop
def handler_other() -> None:
    err: ValueError | None = ValueError("a")
    if err is not None:
        i = 0
        while i < 2:
            y = err  # tpyc: type(ValueError)
            print("handler_other:", y)
            if i == 1:
                try:
                    raise y
                except ValueError as e:
                    pass
            i += 1


# closure defined before a try whose body holds no call: the handler keeps x
def closure_try_nocall() -> None:
    x: int | None = 5
    e = ValueError("raised")

    def clear() -> None:
        nonlocal x
        x = None

    if x is not None:
        try:
            raise e
        except ValueError:
            y = x  # tpyc: type(int)
            print("closure_try_nocall:", y + 1)
    clear()


# a condition call to a closure that writes ANOTHER record: t.v stays narrowed
def field_condition_other() -> None:
    t = N()
    u = N()

    def wipe() -> bool:
        u.v = None
        return True

    if t.v is not None and wipe():
        y = t.v  # tpyc: type(int)
        print("field_condition_other:", y + 1, u.v)


# a closure appending to ANOTHER list keeps the range of i over xs
def receiver_len_other(xs: list[int]) -> None:
    other = [0]

    def grow() -> None:
        other.append(9)

    for i in range(len(xs)):
        grow()
        print("receiver_len_other:", xs[i])  # tpyc: bounds_safe(xs)
    print("receiver_len_other grown:", len(other))


# async body: a call in the loop (no await in it) may run the closure, so the back edge kills t.v
async def async_body() -> None:
    t = N()

    def wipe() -> None:
        t.v = None

    if t.v is not None:
        i = 0
        while i < 2:
            y = t.v  # tpyc: type(/None/)
            print("async_body:", y)
            wipe()
            i += 1


# a closure call in a comprehension condition runs it before the read after
def comprehension_cond() -> None:
    t = N()

    def wipe() -> bool:
        t.v = None
        return True

    if t.v is not None:
        ks = [k for k in range(2) if wipe()]
        y = t.v  # tpyc: type(/None/)
        print("comprehension_cond:", ks, y)


# with body: the call inside it runs the closure
def with_body() -> None:
    t = N()

    def wipe() -> None:
        t.v = None

    if t.v is not None:
        with Scope():
            wipe()
            y = t.v  # tpyc: type(/None/)
            print("with_body:", y)


# a call BEFORE the nested def is defined cannot run it: t.v stays narrowed
def before_def() -> None:
    t = N()
    if t.v is not None:
        print("before_def call")
        y = t.v  # tpyc: type(int)
        print("before_def:", y + 1)

    def wipe() -> None:
        t.v = None

    wipe()


# a def inside the loop body: its writes reach the loop-entry meet
def def_in_loop() -> None:
    t = N()
    if t.v is not None:
        i = 0
        while i < 2:
            y = t.v  # tpyc: type(/None/)
            print("def_in_loop:", y)

            def wipe() -> None:
                t.v = None

            wipe()
            i += 1


# a @readonly method call on the capture: readonly stops writes through
# `self` only (the method may still write what a pointer field reaches), so
# the call kills the facts beneath its receiver like a direct call does
def readonly_receiver() -> None:
    t = N()

    def peek() -> None:
        print("readonly_receiver peek:", t.describe())

    if t.v is not None:
        peek()
        y = t.v  # tpyc: type(/None/)
        print("readonly_receiver:", y)


# the capture passed to a function that stores None through it: o.name dies
def arg_mutating_kills() -> None:
    o = O("a")

    def wipe() -> None:
        clear_name(o)

    if o.name is not None:
        wipe()
        y = o.name  # tpyc: type(/None/)
        print("arg_mutating_kills:", y)


# every store of the closure keeps o.name non-None: the narrowing survives the call
def store_nonnone_kept() -> None:
    o = O("a")

    def rename() -> None:
        o.name = "b"

    if o.name is not None:
        rename()
        print("store_nonnone_kept:", o.name.upper())  # tpyc: ok


def main() -> None:
    capture_loop(None)
    capture_other(None)
    handler_as()
    closure_loop()
    closure_loop_uncalled()
    closure_handler()
    closure_finally()
    field_straight()
    field_loop()
    field_loop_other()
    field_handler()
    field_condition()
    receiver_len([1, 2])
    local_alias()
    projection()
    param_shadow()
    lambda_in_def()
    for _ in gen_capture():
        pass
    N().method_capture()
    handler_other()
    closure_try_nocall()
    field_condition_other()
    receiver_len_other([1, 2])
    asyncio.run(async_body())
    comprehension_cond()
    with_body()
    before_def()
    def_in_loop()
    readonly_receiver()
    arg_mutating_kills()
    store_nonnone_kept()


main()
