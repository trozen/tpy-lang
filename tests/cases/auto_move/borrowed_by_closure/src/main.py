# A name a nested def or lambda captures is never moved anywhere in the
# enclosing body: the closure reads the variable itself whenever it runs --
# later, through a lambda or a stored def, from a `finally`, an `__exit__`
# or a `__del__` after the return, after a rebind -- so every consume of it,
# a `return` included, copies with the owned-storage warning (a @nocopy one
# is an error: error_nocopy_closure_captured_*). The exception is a
# `return` leaving through a `finally`, which moves after the finally
# (`finally_return_defers`). The copy is intended: the
# warning is the designed diagnostic. Most sections write to the object
# through the closure after the consume and read it back, so the CPython
# run checks the closure saw the original; a returned copy is not printed
# where it differs from the object CPython hands back.
from typing import Callable, Iterator
from tpy import Fn, int32, Own


class Point:
    items: list[int32]

    def __init__(self):
        self.items = [1, 2, 3]

    def __str__(self) -> str:
        return "x" * len(self.items)


class Sink:
    stored: list[Point]

    def __init__(self):
        self.stored = []

    def consume(self, p: Own[Point]):
        self.stored.append(p)


def apply(f: Fn[[int32], int32], v: int32) -> int32:
    return f(v)


def run_fn(f: Fn[[], int32], p: Own[Point]) -> int32:
    Sink().consume(p)
    return f()


class Holder:
    f: Callable[[], int32]

    def __init__(self, f: Callable[[], int32]):
        self.f = f

    def __del__(self):
        print("holder sees", self.f())


def run(f: Callable[[], int32], p: Own[Point]) -> int32:
    Sink().consume(p)
    return f()


def sink_len(p: Own[Point]) -> int32:
    p.items.append(5)
    s = Sink()
    s.consume(p)
    return len(s.stored[0].items)


class Ctx:
    f: Callable[[], int32]

    def __init__(self, f: Callable[[], int32]):
        self.f = f

    def __enter__(self) -> None:
        pass

    def __exit__(self, a, b, c) -> None:
        print("with_exit sees", self.f())


class Wrap:
    p: Point

    def __init__(self, p: Own[Point]):
        self.p = p


def once(f: Fn[[], int32]) -> Iterator[int32]:
    yield f()


def each(f: Fn[[int32], int32], xs: list[int32]) -> Iterator[int32]:
    for v in xs:
        yield f(v)


class Hook:
    f: Callable[[], int32]

    def __init__(self, f: Callable[[], int32]):
        self.f = f

    @property
    def value(self) -> int32:
        return self.f()

    def __getitem__(self, i: int32) -> int32:
        return self.f() + i


# free function, Own param: the closure runs in the `return`.
def param_return(x: Own[Point]) -> int32:  # tpyc: warning(/Own\[Point\] param 'x' is never consumed/)
    s = Sink()

    def size() -> int32:
        return len(x.items)

    def grow() -> None:
        x.items.append(7)

    s.consume(x)  # tpyc: warning(/copies Point into owned storage/)
    grow()
    return size()


# indirect reach: the `return` runs a closure that runs the capturing one.
def indirect(x: Own[Point]) -> int32:  # tpyc: warning(/Own\[Point\] param 'x' is never consumed/)
    s = Sink()

    def size() -> int32:
        return len(x.items)

    def outer() -> int32:
        x.items.append(8)
        return size()

    s.consume(x)  # tpyc: warning(/copies Point into owned storage/)
    return outer()


# raise: the raised exception's argument runs the closure.
def raising(x: Own[Point]) -> int32:  # tpyc: warning(/Own\[Point\] param 'x' is never consumed/)
    s = Sink()

    def size() -> int32:
        x.items.append(9)
        return len(x.items)

    s.consume(x)  # tpyc: warning(/copies Point into owned storage/)
    raise ValueError(str(size()))


# lambda: the closure runs inside a lambda passed on in the `return`.
def via_lambda(x: Own[Point]) -> int32:  # tpyc: warning(/Own\[Point\] param 'x' is never consumed/)
    s = Sink()

    def size() -> int32:
        x.items.append(10)
        return len(x.items)

    s.consume(x)  # tpyc: warning(/copies Point into owned storage/)
    return apply(lambda v: v + size(), 100)


# closure never called: still a copy -- the rule does not ask whether the
# closure runs again.
def never_called(x: Own[Point]) -> int32:  # tpyc: warning(/Own\[Point\] param 'x' is never consumed/)
    s = Sink()

    def size() -> int32:
        return len(x.items)

    s.consume(x)  # tpyc: warning(/copies Point into owned storage/)
    return len(s.stored)


# `return x` copies too, even where no closure runs after it.
def return_copies(x: Own[Point]) -> Own[Point]:
    def size() -> int32:
        return len(x.items)

    x.items.append(size())
    return x  # tpyc: warning(/copies Point into owned storage/)


# a consume on a path that leaves before the def copies as well: the rule
# does not ask where the def is.
def def_after_return_copies(x: Own[Point], early: bool) -> int32:  # tpyc: warning(/Own\[Point\] param 'x' is never consumed/)
    if early:
        n = sink_len(x)  # tpyc: warning(/copies Point into owned storage/)
        return n

    def size() -> int32:
        return len(x.items)

    return size()


# ... and before a `raise` (bound first: a copy nested in a builtin call's
# argument does not lower yet, BUGS.md#nested-builtin-arg-own-copy-rejects).
def raise_copies(x: Own[Point], early: bool) -> int32:  # tpyc: warning(/Own\[Point\] param 'x' is never consumed/)
    if early:
        n = sink_len(x)  # tpyc: warning(/copies Point into owned storage/)
        raise ValueError(str(n))

    def size() -> int32:
        return len(x.items)

    return size()


# a lambda held in a local runs the closure inside the call the value is
# consumed by.
def lambda_local(x: Own[Point]) -> int32:  # tpyc: warning(/Own\[Point\] param 'x' is never consumed/)
    def grow() -> int32:
        x.items.append(11)
        return len(x.items)

    k: Callable[[], int32] = lambda: grow()
    return run(k, x)  # tpyc: warning(/copies Point into owned storage/)


# `__exit__` runs the closure after the returned value is built. After the
# `with`, the rebind does not free `x` either: the prescan cannot rule out an
# `__exit__` that swallows and falls through with the lambda still held.
def with_exit(x: Own[Point], flag: bool) -> Own[Wrap]:  # tpyc: warning(/Own\[Point\] param 'x' is never consumed/)
    def grow() -> int32:
        x.items.append(12)
        return len(x.items)

    if flag:
        with Ctx(lambda: grow()):
            return Wrap(x)  # tpyc: warning(/copies Point into owned storage/)
    x = Point()
    return Wrap(x)  # tpyc: warning(/copies Point into owned storage/)


# a call of the closure between the consume and a rebind reads the old
# object: the rebind does not make the consume a last use.
def call_then_rebind(x: Own[Point]) -> int32:  # tpyc: warning(/Own\[Point\] param 'x' is never consumed/)
    s = Sink()

    def grow() -> int32:
        x.items.append(13)
        return len(x.items)

    s.consume(x)  # tpyc: warning(/copies Point into owned storage/)
    print("call_then_rebind", grow())
    x = Point()
    return len(x.items)


# a `return` landing on a `finally` that runs the closure.
def finally_landing(x: Own[Point]) -> int32:  # tpyc: warning(/Own\[Point\] param 'x' is never consumed/)
    s = Sink()

    def grow() -> int32:
        x.items.append(14)
        return len(x.items)

    try:
        s.consume(x)  # tpyc: warning(/copies Point into owned storage/)
        return len(s.stored)
    finally:
        print("finally_landing sees", grow())


# a def passed to a user generator runs at the first pull, after the consume.
def via_generator(x: Own[Point]) -> int32:  # tpyc: warning(/Own\[Point\] param 'x' is never consumed/)
    s = Sink()

    def grow() -> int32:
        x.items.append(15)
        return len(x.items)

    it = once(grow)
    s.consume(x)  # tpyc: warning(/copies Point into owned storage/)
    n = 0
    for v in it:
        n = v
    return n


# `map` holds the def and runs it at each pull.
def via_map(x: Own[Point]) -> int32:  # tpyc: warning(/Own\[Point\] param 'x' is never consumed/)
    s = Sink()

    def grow(i: int32) -> int32:
        x.items.append(16)
        return len(x.items) + i

    m = map(grow, [0])
    s.consume(x)  # tpyc: warning(/copies Point into owned storage/)
    n = 0
    for v in m:
        n = v
    return n


# a callee that runs the def at each later pull.
def via_callee(x: Own[Point]) -> int32:  # tpyc: warning(/Own\[Point\] param 'x' is never consumed/)
    s = Sink()

    def grow(i: int32) -> int32:
        x.items.append(17)
        return len(x.items) + i

    it = each(grow, [0])
    s.consume(x)  # tpyc: warning(/copies Point into owned storage/)
    n = sum(it)
    return n


# the held def runs before a rebind: the rebind frees no earlier consume.
def held_then_rebind(x: Own[Point], out: list[int32]) -> None:  # tpyc: warning(/Own\[Point\] param 'x' is never consumed/)
    s = Sink()

    def grow(i: int32) -> int32:
        x.items.append(18)
        return len(x.items) + i

    m = map(grow, [0])
    s.consume(x)  # tpyc: warning(/copies Point into owned storage/)
    out.append(sum(m))
    x = Point()
    out.append(len(x.items))


# a stored def that names another runs it later without naming it.
def chained() -> int32:
    s = Sink()
    p = Point()

    def size() -> int32:
        return len(p.items)

    def outer() -> int32:
        return size()

    cbs: list[Callable[[], int32]] = []
    cbs.append(outer)
    s.consume(p)  # tpyc: warning(/copies Point into owned storage/)
    n = cbs[0]()
    return n


# a lambda on a path ending in `break` holds the def after the loop.
def held_on_break(flag: bool) -> int32:
    s = Sink()
    p = Point()

    def size() -> int32:
        return len(p.items)

    k: Callable[[], int32] = lambda: 0
    while True:
        if flag:
            k = lambda: size()
            break
        flag = True
    s.consume(p)  # tpyc: warning(/copies Point into owned storage/)
    n = k()
    return n


# a lambda on a path ending in `raise` holds the def in the handler.
def held_on_raise(flag: bool) -> int32:
    s = Sink()
    p = Point()

    def size() -> int32:
        return len(p.items)

    k: Callable[[], int32] = lambda: 0
    try:
        if flag:
            k = lambda: size()
            raise ValueError("held")
    except ValueError:
        pass
    s.consume(p)  # tpyc: warning(/copies Point into owned storage/)
    n = k()
    return n


# a property read in the `return` runs the stored def.
def via_property() -> int32:
    s = Sink()
    p = Point()

    def grow() -> int32:
        p.items.append(22)
        return len(p.items)

    def outer() -> int32:
        return grow()

    h = Hook(outer)
    s.consume(p)  # tpyc: warning(/copies Point into owned storage/)
    return h.value


# an element read in the `return` runs the stored def.
def via_getitem() -> int32:
    s = Sink()
    p = Point()

    def grow() -> int32:
        p.items.append(23)
        return len(p.items)

    def outer() -> int32:
        return grow()

    h = Hook(outer)
    s.consume(p)  # tpyc: warning(/copies Point into owned storage/)
    return h[0]


# a rebind between the consume and the closure call does not lift the pin:
# the closure reads whatever the name holds when it runs.
def rebind_after_consume() -> None:
    s = Sink()
    p = Point()

    def show() -> None:
        print("rebind_after_consume", len(p.items))

    s.consume(p)  # tpyc: warning(/copies Point into owned storage/)
    p = Point()
    p.items.append(4)
    show()


# a lambda set in a handler runs in the `finally` after the consume there.
def finally_after_handler(flag: bool) -> int32:
    s = Sink()
    p = Point()

    def g() -> int32:
        p.items.append(24)
        return len(p.items)

    k: Callable[[], int32] = lambda: 0
    try:
        if flag:
            raise ValueError("e")
    except ValueError:
        k = lambda: g()
    finally:
        s.consume(p)  # tpyc: warning(/copies Point into owned storage/)
        n = k()
        return n


# the one exception: a `return x` leaving through a `finally` is deferred --
# the finally (and the closure it runs) sees the object, which moves out
# after it, as CPython's pending return hands over the same object. A
# closure deleting the name there is error_finally_closure_del_returned_local.
def finally_return_defers(x: Own[Point]) -> Own[Point]:  # tpyc: ok
    def grow() -> int32:
        x.items.append(25)
        return len(x.items)

    try:
        return x  # tpyc: ok
    finally:
        print("finally_return_defers sees", grow())


# a `__del__` runs the closure after the return has built its value: a
# returned name ...
def del_holder_return(x: Own[Point]) -> Own[Point]:
    def g() -> int32:
        x.items.append(26)
        return len(x.items)

    h = Holder(lambda: g())
    return x  # tpyc: warning(/copies Point into owned storage/)


# ... and a returned tuple member.
def del_holder_tuple() -> tuple[Own[Point], int32]:
    p = Point()

    def g() -> int32:
        p.items.append(27)
        return len(p.items)

    h = Holder(lambda: g())
    return (p, 1)  # tpyc: warning(/copies Point into owned storage \(tuple element 0\)/)


# a lambda passed beside the value it reads, into the call that consumes it.
def lambda_same_call() -> int32:
    p = Point()
    return run_fn(lambda: len(p.items), p)  # tpyc: warning(/copies Point into owned storage/)


# ... a returned name at a GENERIC `Own[T]` slot copies as well (`T(x)`), the
# hedged warning's copy: a moved-from `x` would read 0 items. (Read-only: a
# nested def cannot call a bound method on `T`,
# BUGS.md#nested-def-bounded-typeparam-method-rejected.)
def del_holder_generic[T](x: Own[T]) -> Own[T]:
    def g() -> int32:
        return len(str(x))

    h = Holder(lambda: g())
    return x  # tpyc: warning(/may copy T into owned storage/)


# ... and a returned tuple NAME (an `Own` tuple param, which a bare
# `return t;` would move out of).
def del_holder_tuple_name(
        t: tuple[Own[Point], Own[Point]]) -> tuple[Own[Point], Own[Point]]:
    def g() -> int32:
        a, b = t
        a.items.append(29)
        return len(a.items)

    h = Holder(lambda: g())
    return t  # tpyc: warning(/copies tuple\[Own\[Point\], Own\[Point\]\] into owned storage/)


def main():
    s = Sink()
    p = Point()

    def show():
        print(len(p.items))

    s.consume(p)  # tpyc: warning(/copies/)
    show()
    print("param_return", param_return(Point()))
    print("indirect", indirect(Point()))
    try:
        raising(Point())
    except ValueError as e:
        print("raise", e)
    print("lambda", via_lambda(Point()))
    print("never_called", never_called(Point()))
    r = return_copies(Point())
    print("return_copies", len(r.items))
    early = def_after_return_copies(Point(), True)
    late = def_after_return_copies(Point(), False)
    print("def_after_return_copies", early, late)
    try:
        raise_copies(Point(), True)
    except ValueError as e:
        print("raise_copies", e)
    late = raise_copies(Point(), False)
    print("raise_copies", late)
    n = lambda_local(Point())
    print("lambda_local", n)
    with_exit(Point(), True)
    n = call_then_rebind(Point())
    print("call_then_rebind returns", n)
    n = finally_landing(Point())
    print("finally_landing returns", n)
    print("via_generator", via_generator(Point()))
    print("via_map", via_map(Point()))
    print("via_callee", via_callee(Point()))
    out: list[int32] = []
    held_then_rebind(Point(), out)
    print("held_then_rebind", out[0], out[1])
    print("chained", chained())
    print("held_on_break", held_on_break(True))
    print("held_on_raise", held_on_raise(True))
    print("via_property", via_property())
    print("via_getitem", via_getitem())
    rebind_after_consume()
    print("finally_after_handler", finally_after_handler(True))
    r = finally_return_defers(Point())
    print("finally_return_defers", len(r.items))
    r = del_holder_return(Point())
    print("del_holder_return done")
    t = del_holder_tuple()
    print("del_holder_tuple", t[1])
    print("lambda_same_call", lambda_same_call())
    g = del_holder_generic(Point())
    print("del_holder_generic done")
    u = del_holder_tuple_name((Point(), Point()))
    print("del_holder_tuple_name done")


main()
