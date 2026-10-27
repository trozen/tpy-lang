# Bindings of an owned reference tuple at every local position: a copy()
# owns a fresh copy (writes through it never reach the source), a dead name
# hands its storage over, and a borrowed member aliases.
import asyncio
from typing import Callable, Iterator

from tpy import (Copyable, Own, ReturnException, String, ValueType, copy,
                 error_return, int32, nocopy)


class A:
    def __init__(self, v: int32) -> None:
        self.v = v


@nocopy
class N:
    def __init__(self, v: int32) -> None:
        self.v = v


def make_mixed(a: A) -> tuple[Own[A], A]:
    return (A(1), a)


# free function: a copy() declaration, then a rebind to another copy()
def free_fn(t: tuple[A, int32]) -> None:
    q = copy(t)  # tpyc: ok
    q[0].v = 5
    print("free_fn decl", t[0].v, q[0].v)
    q = copy(t)  # tpyc: ok
    q[0].v = 6
    print("free_fn rebind", t[0].v, q[0].v)


# walrus target bound to a copy()
def walrus(t: tuple[A, int32]) -> None:
    print("walrus", (w := copy(t))[0].v)  # tpyc: ok
    w[0].v = 7
    print("walrus after", t[0].v, w[0].v)


# unpack of a copy()
def unpack(t: tuple[A, int32]) -> None:
    a, n = copy(t)  # tpyc: ok
    a.v = 8
    print("unpack", t[0].v, a.v, n)


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    # method: a copy() of a live local tuple
    def snap(self) -> None:
        t = (A(self.n), 0)
        q = copy(t)  # tpyc: ok
        q[0].v = 9
        print("method", t[0].v, q[0].v)


# closure: a nonlocal rebind to a literal of a borrowed name and a live
# local; the local's elements are warned copies, the borrowed name aliases
def closure(b: A) -> None:
    t = make_mixed(b)
    u = (b, t)  # tpyc: warning(/tuple element 1.0/) warning(/tuple element 1.1/)
    def inner() -> None:
        nonlocal u
        u = (b, t)  # tpyc: warning(/tuple element 1.0/) warning(/tuple element 1.1/)
    inner()
    b.v = 11
    print("closure", u[0].v)


# loop: a local rebound to a copy() each iteration
def loop(t: tuple[A, int32]) -> None:
    for i in range(2):
        q = copy(t)  # tpyc: ok
        q[0].v = 20 + i
        print("loop", i, t[0].v, q[0].v)


# match arm: a copy() bound inside a case
def arm(k: int32, t: tuple[A, int32]) -> None:
    match k:
        case 1:
            q = copy(t)  # tpyc: ok
            q[0].v = 30
            print("arm", t[0].v, q[0].v)
        case _:
            print("arm other")


# dead name: t's last use hands its @nocopy element over to u
def dead_member() -> None:
    t = (N(1), 2)
    u = (t, 1)  # tpyc: ok
    u[0][0].v = 40
    print("dead_member", u[0][0].v, u[1])


# generator body: a copy() local kept across a yield
def gen(t: tuple[A, int32]) -> Iterator[int32]:
    q = copy(t)  # tpyc: ok
    q[0].v = 60
    yield t[0].v
    yield q[0].v


# async body: a copy() local kept across an await
async def coro(t: tuple[A, int32]) -> int32:
    q = copy(t)  # tpyc: ok
    q[0].v = 70
    await asyncio.sleep(0)
    return t[0].v * 100 + q[0].v


class Holder:
    n: int32

    # constructor: a copy() of a live local tuple
    def __init__(self, v: int32) -> None:
        t = (A(v), 0)
        q = copy(t)  # tpyc: ok
        q[0].v = 95
        self.n = q[0].v
        print("ctor", t[0].v, q[0].v)


class Bad(Exception, ReturnException):
    pass


# @error_return body: a copy() local
@error_return(Bad)
def er(t: tuple[A, int32]) -> int32:
    q = copy(t)  # tpyc: ok
    q[0].v = 99
    print("error_return", t[0].v, q[0].v)
    return q[0].v


# generic: a ValueType-bounded copy() local returned
def clone[T: ValueType](x: T) -> T:
    y = copy(x)  # tpyc: ok
    return y


# bounded generic: a lambda returning copy() of T
def lam[T: ValueType](x: T) -> T:
    f: Callable[[], T] = lambda: copy(x)  # tpyc: warning(/may keep its own copy of .x./)
    return f()


# bounded generic: an owned T handed back as an Optional result
def opt_back[T: ValueType](x: Own[T]) -> T | None:
    return x


# bounded generic: a tuple result built from the parameter
def pair_back[T: ValueType](x: T) -> tuple[T, int32]:
    return (x, 1)


# bounded generic: an owned tuple with an Optional T element passed through
def own_pair[T: ValueType](x: Own[tuple[T | None, int32]]
                           ) -> Own[tuple[T | None, int32]]:
    return x


# bounded class: a list field built from the parameter
class Items[T: ValueType]:
    items: list[T]

    def __init__(self, v: T) -> None:
        self.items = [v]


class VBox[T: ValueType]:
    v: T

    def __init__(self, v: T) -> None:
        self.v = v


def take[T: ValueType](x: T) -> T:
    return x


# bounded generic: a field write and an argument
def bounded_paths[T: ValueType](b: VBox[T], x: T) -> None:
    b.v = x
    y = take(x)
    print("bounded_paths", y)


# bounded generic: a mixed tuple parameter returned whole (its definition
# is the subject: a call building the argument is refused as on master)
def f_mix[T: ValueType](p: tuple[Own[T], A]) -> tuple[Own[T], A]:
    return p  # tpyc: ok


# finally reads the returned value: a ValueType T is captured deferred
def fin_vt[T: ValueType](x: Own[T]) -> Own[T]:
    try:
        return x  # tpyc: ok
    finally:
        print("fin_vt", x)


# finally reads the returned value: an unbounded T is captured deferred
def fin_open[T](x: Own[T]) -> Own[T]:
    try:
        return x  # tpyc: ok
    finally:
        print("fin_open", x)


# finally reads the returned value: an owned str is captured deferred
def fin_str(x: Own[str]) -> Own[str]:
    try:
        return x  # tpyc: ok
    finally:
        print("fin_str", x)


# finally reads the returned value: an Own[int32] stays eager (cheap copy)
def fin_int(x: Own[int32]) -> Own[int32]:
    try:
        return x  # tpyc: ok
    finally:
        print("fin_int", x)


# finally reads a String local returned at a plain `-> String`: the result
# holds its own storage, so the capture is deferred
def fin_string(s: str) -> String:
    v = String(s)
    try:
        return v  # tpyc: ok
    finally:
        print("fin_string", v)


# finally reads a String local returned at `-> Own[str]` (an identity
# coercion): captured deferred
def fin_string_own(s: str) -> Own[str]:
    v = String(s)
    try:
        return v  # tpyc: ok
    finally:
        print("fin_string_own", v)


# finally reads a str local returned at a plain `-> str`: captured deferred
def fin_plain_str(s: str) -> str:
    v = s + "!"
    try:
        return v  # tpyc: ok
    finally:
        print("fin_plain_str", v)


# finally reads a str parameter returned at `-> str`: the return converts
# the view to an owned string, so the capture stays eager
def fin_view(s: str) -> str:
    try:
        return s  # tpyc: ok
    finally:
        print("fin_view", s)


# finally reads the str member of a tuple literal returned at a plain
# `tuple[str, int32]`: that member is captured deferred
def fin_plain_tuple(s: str) -> tuple[str, int32]:
    v = s + "?"
    try:
        return v, 1  # tpyc: ok
    finally:
        print("fin_plain_tuple", v)


# finally reads, then deletes, the returned name: the capture goes back to
# eager (a parameter and a local)
def fin_del(x: Own[str]) -> Own[str]:
    try:
        return x  # tpyc: ok
    finally:
        print("fin_del", x)
        del x


def fin_del_local(s: str) -> Own[str]:
    x = s + "#"
    try:
        return x  # tpyc: ok
    finally:
        print("fin_del_local", x)
        del x


# finally reads the members of a returned tuple literal
def fin_tuple(x: Own[str], n: int32) -> tuple[Own[str], int32]:
    try:
        return (x, n)  # tpyc: ok
    finally:
        print("fin_tuple", x, n)


class Shadow[T: ValueType]:
    v: T

    def __init__(self, v: T) -> None:
        self.v = v

    # a method's own T shadows its class's: a `T | None` result
    def opt[T: ValueType](self, x: T) -> T | None:
        return x  # tpyc: ok

    # a ValueType T local stored at its last use moves into the field
    def set_local(self, x: T) -> None:
        y = copy(x)
        self.v = y  # tpyc: ok

    # a method's own unbounded T shadows the class's ValueType bound, so
    # the copy into the list keeps its hedged warning
    def keep[T](self, x: T) -> Own[list[T]]:
        return [x]  # tpyc: warning(/may copy T into owned storage/)


# a mixed (Own[A], Own[T: ValueType]) result returned whole
def mixed_own[T: ValueType](p: tuple[Own[A], Own[T]]) -> tuple[Own[A], Own[T]]:
    return p  # tpyc: ok


class CHolder[T: Copyable]:
    def __init__(self) -> None:
        pass

    # a method's own bound overrides its class's: T: ValueType here
    def take[T: ValueType](self, x: Own[T]) -> Own[T]:
        return x  # tpyc: ok


class RHolder[T: ValueType]:
    def __init__(self) -> None:
        pass

    # the reverse: a method's own T: Copyable over its class's ValueType
    # (the definition is the subject; a call building the tuple argument
    # is refused as on master)
    def take[T: Copyable](self, x: tuple[Own[T], A]) -> tuple[Own[T], A]:
        return x  # tpyc: ok

    # the reverse, a parameter rebind (its check runs past settlement)
    def reb[T: Copyable](self, x: tuple[Own[T], A],
                         y: tuple[Own[T], A]) -> tuple[Own[T], A]:
        x = y  # tpyc: ok
        return x


class VHolder[T: ValueType]:
    v: T
    xs: list[T]

    def __init__(self, v: T) -> None:
        self.v = v
        self.xs = []

    # a bounded class's method: an owned T stored, returned and appended
    def store(self, x: Own[T]) -> None:
        self.v = x  # tpyc: ok

    def give(self, x: Own[T]) -> Own[T]:
        return x  # tpyc: ok

    def push(self, x: Own[T]) -> None:
        self.xs.append(x)  # tpyc: ok


def main() -> None:
    t = (A(1), 2)
    free_fn(t)
    walrus(t)
    unpack(t)
    Box(3).snap()
    closure(A(10))
    loop(t)
    arm(1, t)
    arm(2, t)
    dead_member()
    module_read()
    for v in gen(t):
        print("gen", v)
    print("async", asyncio.run(coro(t)))
    Holder(1)
    try:
        n = er(t)
        print("er", n)
    except Bad:
        print("er failed")
    print("clone", clone(5))
    print("lam", lam(8), lam("s"))
    print("opt_back", opt_back(5))
    print("pair_back", pair_back(3))
    y = own_pair((3, 2))
    print("own_pair", y[1])
    it = Items("s")
    print("items", it.items[0])
    vb = VBox("u")
    bounded_paths(vb, "t")
    print("bounded_paths after", vb.v)
    print("method_bound", CHolder[int32]().take(2))
    vh = VHolder("a")
    vh.store("b")
    vh.push("c")
    print("class_bound", vh.v, vh.give("d"), vh.xs[0])
    r1 = fin_vt("hello")
    print("fin_vt ret", r1)
    r2 = fin_open("open")
    print("fin_open ret", r2)
    r3 = fin_str("str")
    print("fin_str ret", r3)
    r4 = fin_int(7)
    print("fin_int ret", r4)
    r5 = fin_string("strg")
    print("fin_string ret", r5)
    r6 = fin_string_own("so")
    print("fin_string_own ret", r6)
    r7 = fin_plain_str("ps")
    print("fin_plain_str ret", r7)
    r8 = fin_view("vw")
    print("fin_view ret", r8)
    pt, pn = fin_plain_tuple("pt")
    print("fin_plain_tuple ret", pt, pn)
    r9 = fin_del("dl")
    print("fin_del ret", r9)
    r10 = fin_del_local("dk")
    print("fin_del_local ret", r10)
    ft, fn = fin_tuple("tup", 3)
    print("fin_tuple ret", ft, fn)
    sh = Shadow[int32](1)
    o = sh.opt(5)
    if o is not None:
        print("shadow_opt", o + 1)
    sh.set_local(9)
    print("set_local", sh.v)
    kept = sh.keep(4)
    print("shadow_keep", kept[0])
    ma, mn = mixed_own((A(4), 5))
    print("mixed_own", ma.v, mn)



# module: a copy() of a module tuple, written and read from a function
mt = (A(1), 2)
mq = copy(mt)  # tpyc: ok


def module_read() -> None:
    mq[0].v = 50
    print("module", mt[0].v, mq[0].v)


main()
