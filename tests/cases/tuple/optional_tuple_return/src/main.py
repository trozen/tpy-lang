# A nullable tuple return (`-> tuple[...] | None`) holding a reference is
# returned and received like its bare twin: the optional holds the tuple's
# return layout, so borrowed elements alias the caller's objects (each
# section writes through one and prints the object it reached).
from enum import Enum

from tpy import int32, Own, readonly


class Color(Enum):
    Red = 1
    Blue = 2


class Box:
    def __init__(self, n: int32) -> None:
        self.n = n


class Holder:
    k: int32
    item: Box
    pair: tuple[Box, int32]

    def __init__(self, k: int32) -> None:
        self.k = k
        self.item = Box(k)
        self.pair = (Box(k + 1), k)

    # method: a whole tuple field, lifted to its borrow form.
    def maybe_pair(self, k: bool) -> tuple[Box, int32] | None:
        if k:
            return None  # tpyc: ok
        return self.pair  # tpyc: ok

    # method: a borrowed field beside a value.
    def maybe(self, k: bool) -> tuple[Box, int32] | None:
        if k:
            return None  # tpyc: ok
        return (self.item, self.k)  # tpyc: ok

    # method: a fresh owned element beside a borrowed field.
    def maybe_mixed(self, k: bool) -> tuple[Own[Box], Box] | None:
        if k:
            return None  # tpyc: ok
        return (Box(self.k * 10), self.item)  # tpyc: ok

    # method: an owned element beside a value.
    def maybe_owned(self, k: bool) -> tuple[Own[Box], int32] | None:
        if k:
            return None  # tpyc: ok
        return (Box(self.k * 100), self.k)  # tpyc: ok

    # @readonly method: the field is handed out through a const element.
    @readonly
    def peek(self, k: bool) -> tuple[Box, int32] | None:
        if k:
            return None  # tpyc: ok
        return (self.item, self.k)  # tpyc: ok


# free function: every element borrowed.
def both(a: Box, b: Box, k: bool) -> tuple[Box, Box] | None:
    if k:
        return None  # tpyc: ok
    return (a, b)  # tpyc: ok


# free function: mixed.
def mixed(b: Box, k: bool) -> tuple[Own[Box], Box] | None:
    if k:
        return None  # tpyc: ok
    return (Box(7), b)  # tpyc: ok


# free function: every element owned.
def owned(k: bool) -> tuple[Own[Box], Own[Box]] | None:
    if k:
        return None  # tpyc: ok
    return (Box(1), Box(2))  # tpyc: ok


# relays: a nullable local and a nullable call, returned whole.
def relay(a: Box, b: Box, k: bool) -> tuple[Box, Box] | None:
    t = both(a, b, k)  # tpyc: ok
    return t  # tpyc: ok


def relay_call(a: Box, b: Box, k: bool) -> tuple[Box, Box] | None:
    return both(a, b, k)  # tpyc: ok


def relay_mixed(b: Box, k: bool) -> tuple[Own[Box], Box] | None:
    return mixed(b, k)  # tpyc: ok


def relay_owned(k: bool) -> tuple[Own[Box], Own[Box]] | None:
    t = owned(k)  # tpyc: ok
    return t  # tpyc: ok


def relay_owned_call(k: bool) -> tuple[Own[Box], Own[Box]] | None:
    return owned(k)  # tpyc: ok


# try/finally: the returned value is captured before the finally runs.
def guarded(a: Box, b: Box, k: bool) -> tuple[Box, Box] | None:
    try:
        if k:
            return None  # tpyc: ok
        return (a, b)  # tpyc: ok
    finally:
        a.n += 100


# try/finally: an owned nullable tuple local returned under a finally that
# writes through it hands back the written tuple (deferred past the chain).
def guarded_owned(k: bool) -> tuple[Own[Box], int32] | None:
    r = owned_pair(k)
    try:
        return r  # tpyc: ok
    finally:
        if r is not None:
            r[0].n += 10


def owned_pair(k: bool) -> tuple[Own[Box], int32] | None:
    if k:
        return None
    return (Box(1), 2)


# match arm.
def by_match(a: Box, b: Box, k: int32) -> tuple[Box, Box] | None:
    match k:
        case 0:
            return None  # tpyc: ok
        case _:
            return (b, a)  # tpyc: ok


# Borrowed elements rooted in a parameter through a local: an alias, a loop
# variable and an element read.
def via_alias(p: Box) -> tuple[Box, int32] | None:
    c = p
    return (c, 1)  # tpyc: ok


def via_loop(xs: list[Box]) -> tuple[Box, int32] | None:
    for x in xs:
        return (x, 2)  # tpyc: ok
    return None


def via_elem(xs: list[Box]) -> tuple[Box, int32] | None:
    y = xs[1]
    return (y, 3)  # tpyc: ok


# The bare twin of via_loop: the loop variable is lent writable, so it
# binds mutable.
def first_bare(xs: list[Box]) -> tuple[Box, int32]:
    for x in xs:
        return (x, 4)  # tpyc: ok
    return (xs[0], 0)


# An enum element beside a borrowed one, bare and nullable: the enum
# spells the same value in both forms, like a scalar.
def tagged(b: Box, c: Color) -> tuple[Box, Color]:
    return (b, c)  # tpyc: ok


def maybe_tagged(b: Box, c: Color, k: bool) -> tuple[Box, Color] | None:
    if k:
        return None  # tpyc: ok
    return (b, c)  # tpyc: ok


# A bare `return` at the nullable slot is the None leg.
def bare_return() -> tuple[Box, int32] | None:
    return  # tpyc: ok


def section_free() -> None:
    a = Box(1)
    b = Box(2)
    t = both(a, b, False)  # tpyc: ok
    if t is not None:
        t[1].n += 5  # tpyc: ok
    print("free both:", a.n, b.n, both(a, b, True) is None)
    m = mixed(b, False)  # tpyc: ok
    if m is not None:
        m[0].n += 1
        m[1].n += 5
        print("free mixed:", m[0].n, b.n)
    print("free mixed none:", mixed(b, True) is None)
    f = owned(False)  # tpyc: ok
    if f is not None:
        f[0].n += 40
        print("free owned:", f[0].n + f[1].n)
    print("free owned none:", owned(True) is None)


def section_method() -> None:
    h = Holder(4)
    t = h.maybe(False)  # tpyc: ok
    if t is not None:
        t[0].n += 10
    print("method:", h.item.n, h.maybe(True) is None)
    m = h.maybe_mixed(False)  # tpyc: ok
    if m is not None:
        m[1].n += 1
        print("method mixed:", m[0].n, h.item.n, h.maybe_mixed(True) is None)
    p = h.maybe_pair(False)  # tpyc: ok
    if p is not None:
        p[0].n += 20
    print("method field:", h.pair[0].n, h.maybe_pair(True) is None)
    o = h.maybe_owned(False)  # tpyc: ok
    if o is not None:
        print("method owned:", o[0].n, o[1], h.maybe_owned(True) is None)
    r = h.peek(False)  # tpyc: ok
    if r is not None:
        print("readonly:", r[0].n, r[1], h.peek(True) is None)


def section_relay() -> None:
    a = Box(1)
    b = Box(2)
    t = relay(a, b, False)
    if t is not None:
        t[0].n += 10
    u = relay_call(a, b, False)
    if u is not None:
        u[1].n += 10
    print("relay:", a.n, b.n, relay(a, b, True) is None,
          relay_call(a, b, True) is None)
    m = relay_mixed(b, False)
    if m is not None:
        m[1].n += 1
        print("relay mixed:", m[0].n, b.n)
    o = relay_owned(False)
    c = relay_owned_call(False)
    if o is not None and c is not None:
        print("relay owned:", o[0].n, c[1].n, relay_owned(True) is None)


def section_finally() -> None:
    a = Box(1)
    b = Box(2)
    t = guarded(a, b, False)
    if t is not None:
        t[1].n += 10
        print("finally:", t[0].n, a.n, b.n)
    print("finally none:", guarded(a, b, True) is None, a.n)
    g = guarded_owned(False)
    if g is not None:
        print("finally owned:", g[0].n, guarded_owned(True) is None)


def section_match() -> None:
    a = Box(1)
    b = Box(2)
    t = by_match(a, b, 1)
    if t is not None:
        t[0].n += 3
    print("match:", b.n, by_match(a, b, 0) is None)


def section_roots() -> None:
    xs = [Box(1), Box(2)]
    p = Box(5)
    t = via_alias(p)
    if t is not None:
        t[0].n += 10
    u = via_loop(xs)
    if u is not None:
        u[0].n += 20
    v = via_elem(xs)
    if v is not None:
        v[0].n += 30
    w = first_bare(xs)
    w[0].n += 40
    empty: list[Box] = []
    print("roots:", p.n, xs[0].n, xs[1].n, via_loop(empty) is None)
    print("bare return:", bare_return() is None)
    # Truthiness of the nullable tuple: engaged is truthy.
    y = both(p, xs[0], False)
    if y:
        y[0].n += 1
    print("truthy:", p.n, not both(p, xs[0], True))


def section_enum() -> None:
    b = Box(3)
    t = tagged(b, Color.Blue)
    t[0].n += 1
    m = maybe_tagged(b, Color.Red, False)
    if m is not None:
        m[0].n += 10
    print("enum:", b.n, t[1] == Color.Blue, m is not None and m[1] == Color.Red,
          maybe_tagged(b, Color.Red, True) is None)


def main() -> None:
    section_free()
    section_method()
    section_relay()
    section_finally()
    section_match()
    section_roots()
    section_enum()


main()
