# A `@dynamic` protocol local, reseat, global or argument aliases an object it
# does not own and owns only a fresh value or a value type's copy.
from typing import Callable, Protocol

from tpy import Own, ReturnException, ValueType, dynamic, error_return, int32, nocopy, readonly


@dynamic
class Shape(Protocol):
    @readonly
    def area(self) -> int32: ...

    def grow(self) -> None: ...


@dynamic
class Area(Protocol):
    @readonly
    def area(self) -> int32: ...


class Sq:
    s: int32

    def __init__(self, s: int32) -> None:
        self.s = s

    @readonly
    def area(self) -> int32:
        return self.s * self.s

    def grow(self) -> None:
        self.s += 1


@nocopy
class Pinned:
    s: int32

    def __init__(self, s: int32) -> None:
        self.s = s

    @readonly
    def area(self) -> int32:
        return self.s * self.s

    def grow(self) -> None:
        self.s += 1


class Dog(Shape):
    s: int32

    def __init__(self, s: int32) -> None:
        self.s = s

    @readonly
    def area(self) -> int32:
        return self.s * self.s

    def grow(self) -> None:
        self.s += 1


class V(ValueType):
    s: int32

    def __init__(self, s: int32) -> None:
        self.s = s

    @readonly
    def area(self) -> int32:
        return self.s * self.s


class VI(ValueType, Area):
    s: int32

    def __init__(self, s: int32) -> None:
        self.s = s

    @readonly
    def area(self) -> int32:
        return self.s * self.s


class Bad(Exception, ReturnException):
    pass


class Ctx:
    def __enter__(self) -> int32:
        return 0

    def __exit__(self, et, ev, tb) -> None:
        pass


def make_sq(n: int32) -> Own[Sq]:
    return Sq(n)


# free function: a local and a parameter source are aliased
def free_function(q: Sq) -> None:
    s: Shape = q  # tpyc: ok
    s.grow()
    r = Sq(4)
    t: Shape = r  # tpyc: ok
    r.grow()
    print("free_function", s.area(), q.s, t.area())


# inferred-const parameter: `q` is only read, so it binds const; a write
# through `m` (the same object) shows through both protocol locals
def inferred_const(q: Sq, m: Sq) -> None:
    s: Area = q  # tpyc: ok
    t: Shape = q  # tpyc: ok
    m.s += 1
    print("inferred_const", s.area(), t.area())


# readonly parameter at a readonly-only and at a mixed protocol
def readonly_param(q: readonly[Sq], m: Sq) -> None:
    s: Area = q  # tpyc: ok
    t: Shape = q  # tpyc: ok
    m.s += 1
    print("readonly_param", s.area(), t.area())


# hoisted owned slot and narrowed optional sources
def pointer_sources(o: Sq | None) -> None:
    for _i in range(1):
        b = Sq(2)
    s: Shape = b  # tpyc: ok
    s.grow()
    print("pointer_sources", s.area(), b.s)
    if o is not None:
        t: Shape = o  # tpyc: ok
        t.grow()
        print("pointer_sources", t.area(), o.s)


# @nocopy source: aliased, so nothing is copied
def nocopy_source(n: Pinned) -> None:
    s: Shape = n  # tpyc: ok
    s.grow()
    print("nocopy_source", s.area(), n.s)


# inheritance conformer: the local is the object's address (mutable and
# const source), a reseat to a fresh Dog owns it, a reseat back aliases
def inheritance(d: Dog, c: Dog) -> None:
    s: Shape = d  # tpyc: ok
    s.grow()
    k: Shape = c  # tpyc: ok
    print("inheritance", s.area(), d.s, k.area())
    s = Dog(5)  # tpyc: ok
    s.grow()
    print("inheritance", s.area(), d.s)
    s = d  # tpyc: ok
    s.grow()
    print("inheritance", s.area(), d.s)


# value type: a name binding copies (the rebind of `v` is not seen through `s`)
def value_type() -> None:
    v = V(2)
    s: Area = v  # tpyc: ok
    w = VI(3)
    t: Area = w  # tpyc: ok
    v = V(5)
    w = VI(6)
    print("value_type", s.area(), v.s, t.area(), w.s)


# rvalue ctor / call: the local owns the fresh object
def rvalues(q: Sq) -> None:
    s: Shape = Sq(q.s)  # tpyc: ok
    s.grow()
    t: Shape = make_sq(7)  # tpyc: ok
    t.grow()
    print("rvalues", s.area(), q.s, t.area())


# protocol-typed source: aliased
def protocol_source(p: Shape) -> None:
    s: Shape = p  # tpyc: ok
    s.grow()
    print("protocol_source", s.area(), p.area())


# method and constructor bodies
class User:
    k: int32

    def __init__(self, q: Sq) -> None:
        s: Shape = q  # tpyc: ok
        s.grow()
        self.k = q.s
        print("constructor", s.area(), q.s)

    def method(self, q: Sq) -> None:
        s: Shape = q  # tpyc: ok
        s.grow()
        print("method", s.area(), q.s, self.k)


# @error_return body
@error_return(Bad)
def error_return_body(q: Sq) -> int32:
    s: Shape = q  # tpyc: ok
    s.grow()
    if q.s < 0:
        raise Bad
    print("error_return_body", s.area(), q.s)
    return q.s


# closure reading the local: it sees the object, not a copy; the capture copies
# the local's pointer, so the hedged warning is spurious
# (BUGS.md#hedged-capture-copy-unrebound-param)
def closure(q: Sq) -> None:
    s: Shape = q
    f: Callable[[], int32] = lambda: s.area()  # tpyc: warning(/may keep its own copy of 's'/)
    q.grow()
    print("closure", f(), q.s)


# first binding inside if / try / while / with / for, lvalue and rvalue
def at_if(q: Sq, n: int32) -> None:
    if n > 0:
        s: Shape = q  # tpyc: ok
    else:
        s = Sq(n)  # tpyc: ok
    s.grow()
    print("at_if", n, s.area(), q.s)


def at_try(q: Sq) -> None:
    try:
        s: Shape = q  # tpyc: ok
    except ValueError:
        s = Sq(0)
    s.grow()
    try:
        t: Shape = Sq(4)  # tpyc: ok
    except ValueError:
        t = q
    t.grow()
    print("at_try", s.area(), q.s, t.area())


def at_while(q: Sq) -> None:
    while True:
        s: Shape = q  # tpyc: ok
        t: Shape = Sq(4)  # tpyc: ok
        break
    s.grow()
    t.grow()
    print("at_while", s.area(), q.s, t.area())


def at_with(q: Sq) -> None:
    with Ctx():
        s: Shape = q  # tpyc: ok
        t: Shape = Sq(4)  # tpyc: ok
    s.grow()
    t.grow()
    print("at_with", s.area(), q.s, t.area())


def at_for(q: Sq) -> None:
    for _i in range(1):
        s: Shape = q  # tpyc: ok
        t: Shape = Sq(4)  # tpyc: ok
    s.grow()
    t.grow()
    print("at_for", s.area(), q.s, t.area())


# block-local: declared and used inside an if and a loop body, lvalue and
# rvalue
def block_local(q: Sq, c: bool, k: int32) -> None:
    if c:
        s: Shape = q  # tpyc: ok
        s.grow()
        print("block_local", s.area(), q.s)
    if c:
        r: Shape = Sq(k)  # tpyc: ok
        r.grow()
        print("block_local", r.area())
    for _i in range(2):
        t: Shape = q  # tpyc: ok
        t.grow()
        print("block_local", t.area(), q.s)


# block-local escape: an outer name keeps a protocol local declared in an if
# body, whose slot outlives the block (a loop body's is refused,
# BUGS.md#dyn-protocol-escape-hoisted-decl)
def block_escape(q: Sq, c: bool) -> None:
    t: Shape = Sq(100)
    if c:
        s: Shape = q  # tpyc: ok
        t = s
    u: Shape = Sq(100)
    if c:
        r: Shape = Sq(5)  # tpyc: ok
        u = r
    q.grow()
    t.grow()
    u.grow()
    print("block_escape", t.area(), u.area(), q.s)


def look(s: Shape) -> int32:
    return s.area()


def look_ro(s: readonly[Shape]) -> int32:
    return s.area()


# const sources as call arguments: an inferred-const parameter and a
# readonly one borrow through a const RefAdapter temp
def const_args(q: Sq, r: readonly[Sq], m: Sq) -> None:
    m.s += 1
    print("const_args", look(q), look_ro(r), m.s)  # tpyc: ok


def opt_look(s: Shape | None) -> int32:
    if s is None:
        return 0
    return s.area()


def opt_look_ro(s: readonly[Shape | None]) -> int32:
    if s is None:
        return 0
    return s.area()


# `Shape | None` parameter: a hoisted owned slot, a narrowed optional and a
# readonly source enter by the same rule
def optional_param(o: Sq | None, q: readonly[Sq], m: Sq) -> None:
    for _i in range(1):
        b = Sq(2)
    b.grow()
    print("optional_param", opt_look(b), b.s)  # tpyc: ok
    if o is not None:
        o.grow()
        print("optional_param", opt_look(o), o.s)  # tpyc: ok
    m.s += 1
    print("optional_param", opt_look_ro(q), m.s)  # tpyc: ok


# inheritance conformer first bound at try and with, rvalue and lvalue
def inherit_try_with(d: Dog) -> None:
    try:
        p: Shape = Dog(3)  # tpyc: ok
        k: Shape = d  # tpyc: ok
    except ValueError:
        p = Dog(0)
        k = Dog(0)
    p.grow()
    k.grow()
    print("inherit_try_with", p.area(), k.area(), d.s)
    with Ctx():
        p2: Shape = Dog(5)  # tpyc: ok
        k2: Shape = d  # tpyc: ok
    p2.grow()
    k2.grow()
    print("inherit_try_with", p2.area(), k2.area(), d.s)


# hoisted readonly source: the hoist predeclares a const pointer
def hoisted_readonly(q: readonly[Sq], m: Sq) -> None:
    for _i in range(1):
        s: Shape = q  # tpyc: ok
    m.s += 1
    print("hoisted_readonly", s.area(), m.s)


# isinstance narrows a protocol local bound from a const source
def isinstance_const(q: readonly[Sq], m: Sq) -> None:
    s: Shape = q
    m.s += 1
    if isinstance(s, Sq):  # tpyc: ok
        print("isinstance_const", s.s, s.area())


# loop refill: each pass refills the slot `s` names, which `t` still points
# at -- the warned divergence; the loop runs one pass, so the output matches
# CPython
def loop_refill(xs: list[Sq]) -> None:
    t: Shape = Sq(100)
    s: Shape = Sq(50)
    i = 0
    for q in xs:
        s = q  # tpyc: warning(/'t' will not keep the object it was given -- 's' is rebound here and both names share its storage; bind the new value to a name of its own/)
        if i == 0:
            t = s
        i += 1
    t.grow()
    print("loop_refill", xs[0].s, s.area(), len(xs))


def main() -> None:
    free_function(Sq(2))
    x = Sq(2)
    inferred_const(x, x)
    y = Sq(3)
    readonly_param(y, y)
    pointer_sources(Sq(3))
    nocopy_source(Pinned(2))
    inheritance(Dog(2), Dog(4))
    value_type()
    rvalues(Sq(2))
    protocol_source(Sq(4))
    u = User(Sq(2))
    u.method(Sq(3))
    try:
        error_return_body(Sq(2))
    except Bad:
        print("error_return_body raised")
    closure(Sq(2))
    at_if(Sq(2), 1)
    at_if(Sq(2), 0)
    at_try(Sq(2))
    at_while(Sq(2))
    at_with(Sq(2))
    at_for(Sq(2))
    block_local(Sq(2), True, 4)
    block_escape(Sq(2), True)
    z = Sq(2)
    const_args(z, z, z)
    optional_param(Sq(3), z, z)
    inherit_try_with(Dog(2))
    h = Sq(3)
    hoisted_readonly(h, h)
    n = Sq(4)
    isinstance_const(n, n)
    loop_refill([Sq(2)])


main()

# module global: the global aliases the module's object.
G = Sq(2)
D = Dog(3)
# The rebind warning has no loop here (BUGS.md#dyn-global-alias-warns-loop-rebind).
S: Shape = G  # tpyc: warning(/'S' will not keep the object it was given/)
S.grow()
print("module_global", S.area(), G.s)
T: Shape = D  # tpyc: warning(/'T' will not keep the object it was given/)
T.grow()
print("module_global", T.area(), D.s)
