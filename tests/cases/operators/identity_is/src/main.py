# `a is b` between two objects compares their addresses: True after every
# aliasing boundary, False after a warned copy and between distinct objects.
from __future__ import annotations

import asyncio
from typing import Iterator
from tpy import Array, Ptr, ReturnException, error_return, int8, int32, readonly
from tplib import Rc


class A:
    n: int

    def __init__(self, n: int) -> None:
        self.n = n

    # method: the receiver against an argument
    def same(self, other: A) -> bool:
        return self is other  # tpyc: ok


class Node:
    n: int
    next: Ptr[Node]

    def __init__(self, n: int) -> None:
        self.n = n
        self.next = None


class Holder:
    kid: A | None
    other: A | None
    a: A

    def __init__(self) -> None:
        self.kid = None
        self.other = None
        self.a = A(0)

    # a borrow return of a field
    def first(self) -> A:
        return self.a

    def set_kid(self, n: int) -> None:
        self.kid = A(n)


class Outer:
    h: Holder

    def __init__(self) -> None:
        self.h = Holder()

    # method: a narrowed field reached through a field of `self`
    def has(self, a: A) -> bool:
        if self.h.kid is not None:
            return self.h.kid is a  # tpyc: ok
        return False

    def kid_is_kid(self) -> bool:
        if self.h.kid is not None:
            return self.h.kid is self.h.kid  # tpyc: ok
        return False


class Pair:
    same: bool

    # constructor: two parameters
    def __init__(self, a: A, b: A) -> None:
        self.same = a is b  # tpyc: ok


class Base:
    x: int

    def __init__(self) -> None:
        self.x = 1


class Left:
    l: int

    def __init__(self) -> None:
        self.l = 1


class Right:
    r: int

    def __init__(self) -> None:
        self.r = 2


# two bases: the `Right` part of a `Both` sits at an offset inside it
class Both(Left, Right):
    def __init__(self) -> None:
        Left.__init__(self)
        Right.__init__(self)


class Empty:
    pass


class Cell:
    arr: Array[int32, 2]

    def __init__(self) -> None:
        self.arr = [1, 2]


class Derived(Base):
    def __init__(self) -> None:
        super().__init__()


class Res:
    n: int

    def __init__(self) -> None:
        self.n = 0

    def __enter__(self) -> Res:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


class Missing(Exception, ReturnException):
    pass


G = A(7)
H = A(7)


def glob() -> A:
    return G

# module-level statement: a global record
print("module", G is glob(), G is H, G is not H)  # tpyc: ok




# a narrowed Ptr walk: `cur` is reseated along the chain
def walk(cur: Ptr[Node], target: Node) -> bool:
    while cur is not None:
        if cur is target:  # tpyc: ok
            return True
        cur = cur.next
    return False


def find(xs: list[A], n: int) -> A | None:
    for x in xs:
        if x.n == n:
            return x
    return None


# MIR has no arm for object identity yet (TODO.md "MIR models object identity")
def as_base(b: Base, d: Derived) -> bool:  # tpyc: mir(uncovered /unsupported expression/)
    return b is d  # tpyc: ok


# generator: a parameter yielded back is the argument itself
def echo(a: A) -> Iterator[A]:
    yield a


def gen_pos(a: A, b: A, c: A) -> Iterator[bool]:
    yield a is c  # tpyc: ok
    yield a is b


async def async_pos(a: A, b: A) -> bool:
    await asyncio.sleep(0)
    return a is b  # tpyc: ok


@error_return(Missing)
def er_pos(a: A, b: A) -> bool:
    if a is not b:  # tpyc: ok
        raise Missing
    return True


# free function: local alias, distinct objects, `is not`
def free_fn() -> None:
    a = A(1)
    b = A(1)
    c = a
    print("free", a is c, a is b, a is not b, a is a)  # tpyc: ok


def method_fn() -> None:
    a = A(1)
    b = A(1)
    print("method", a.same(a), a.same(b))
    print("ctor", Pair(a, a).same, Pair(a, b).same)


def container_fn() -> None:
    ys = [1, 2]
    zs = ys
    ws = [1, 2]
    print("list", ys is zs, ys is ws)  # tpyc: ok
    d = {"k": 1}
    d2 = d
    e = {"k": 1}
    print("dict", d is d2, d is e)  # tpyc: ok
    st = {1, 2}
    st2 = st
    st3 = {1, 2}
    print("set", st is st2, st is st3)  # tpyc: ok
    xs = [A(1), A(2)]
    found = 0
    # for-loop variable over list[A]: each is the element itself
    for x in xs:
        if x is xs[found]:  # tpyc: ok
            found += 1
    print("loop", found)


# two `Ptr` parameters: identity is pointee identity
def same_ptr(p: Ptr[A], q: Ptr[A]) -> bool:
    return p is q  # tpyc: ok


def arr_first(a: Array[int32, 2]) -> int32:
    return a[0]


# containers of one kind compare whatever their element types: `is` decides
# no types, so a literal-seeded local keeps the type its other uses give it
def literal_fn(ys: list[int], d: dict[str, float], wide: list[int32],
               fset: set[float]) -> None:
    zs = [1, 2]
    print("literal", ys is zs, arr_first(zs))  # tpyc: ok
    fs = [1.5]
    strs = ["x"]
    print("literal mixed", zs is fs, zs is strs)  # tpyc: ok
    e = {"k": 1}
    print("literal dict", d is e)  # tpyc: ok
    cs = [x for x in range(3)]
    print("literal comp", ys is cs, wide is ys)  # tpyc: ok
    small: list[int8] = [1]
    ws = []
    vs = ws
    print("literal empty", small is ws, ws is vs)  # tpyc: ok
    ws.append(100)
    ws.append(ws[0] + ws[0])
    print("literal empty after", ws)
    st = {1, 2}
    print("literal set", fset is st)  # tpyc: ok
    # an `is` leaves a literal's elements open: the append decides them
    ps = [1, 2]
    qs = [3]
    print("literal open", ps is qs, ps is ps)  # tpyc: ok
    ps.append(3000000000)
    print("literal open after", ps)


# containers of one kind at an address shared with another object
def nested_fn(a: Array[Array[int32, 2], 2]) -> None:
    zs = [[1, 2], [3, 4]]
    # an Array's first element sits at the Array's own address
    print("nested", zs[0] is zs, a[0] is a, a is not a[0])  # tpyc: ok
    rs: Array[Cell, 2] = [Cell(), Cell()]
    print("nested field", rs is rs[0].arr, rs is rs[1].arr)  # tpyc: ok


# two Nones of different container types are one None
def none_pair_fn(xs: list[int] | None, ys: list[int]) -> None:
    a: list[int] | None = None
    b: list[float] | None = None
    print("none pair", a is b, xs is b, xs is ys)  # tpyc: ok


def as_right(r: Right, b: Both) -> bool:
    return r is b  # tpyc: ok


# a readonly view of an object is that object
def ro_same(r: readonly[A], a: A) -> bool:
    return r is a  # tpyc: ok


def objects_fn() -> None:
    a = A(1)
    print("readonly alias", ro_same(a, a))
    b = Both()
    # the `Right` base of `b` is `b` itself
    print("two bases", as_right(b, b), as_right(Both(), b))
    e1 = Empty()
    e2 = Empty()
    print("empty records", e1 is e2)  # tpyc: ok


# a narrowed field read inside a closure
def closure_narrow_fn() -> None:
    h = Holder()
    h.set_kid(5)
    a = A(1)

    def inner() -> bool:
        if h.kid is not None:
            return h.kid is a  # tpyc: ok
        return False

    def inner_same() -> bool:
        if h.kid is not None:
            return h.kid is h.kid  # tpyc: ok
        return False

    def inner_value() -> int:
        if h.kid is not None:
            # a value read of the narrowed field through the captured name
            return h.kid.n  # tpyc: ok
        return 0

    print("closure narrowed", inner(), inner_same(), inner_value())


def first_ptr(xs: list[A]) -> Ptr[A]:
    return xs[0]


# a narrowed field through a `Ptr` receiver, as an `is` operand
def via_ptr(h: Ptr[Holder], a: A) -> bool:
    if h is not None and h.kid is not None:
        return h.kid is a  # tpyc: ok
    return False


# ... and returned at a pointer-repr Optional result
def kid_via_ptr(h: Ptr[Holder]) -> A | None:
    if h is not None and h.kid is not None:
        return h.kid  # tpyc: ok
    return None


# the un-narrowed field returned through a `Ptr` receiver, mutable or
# readonly: the storage optional lifts, None included
def kid_or_none(h: Ptr[Holder]) -> A | None:
    if h is not None:
        return h.kid  # tpyc: ok
    return None


def kid_or_none_ro(h: Ptr[readonly[Holder]]) -> readonly[A] | None:
    if h is not None:
        return h.kid  # tpyc: ok
    return None


# a value read of a narrowed field through a chained receiver
def chain_value(o: Outer) -> int:
    if o.h.kid is not None:
        return o.h.kid.n  # tpyc: ok
    return 0


# a pointer against itself
def ptr_self(p: Ptr[A]) -> bool:
    return p is p  # tpyc: ok


# a narrowed field through a chained receiver
def via_chain(o: readonly[Outer], a: A) -> bool:
    if o.h.kid is not None:
        return o.h.kid is a  # tpyc: ok
    return False


def ptr_fn() -> None:
    a = Node(1)
    b = Node(2)
    c = Node(3)
    a.next = b
    print("ptr", walk(a, b), walk(a, c))
    x = A(1)
    y = A(1)
    print("ptr pair", same_ptr(x, x), same_ptr(x, y), ptr_self(x))
    xs = [A(1), A(2)]
    # a `Ptr` a call hands back points at an existing object
    print("ptr call", first_ptr(xs) is xs[0], first_ptr(xs) is xs[1])  # tpyc: ok


def optional_fn() -> None:
    h = Holder()
    # two storage fields, both None
    print("opt none", h.kid is h.other, h.kid is h.kid)  # tpyc: ok
    a = A(1)
    h.kid = A(2)
    print("opt set", h.kid is h.kid, h.kid is h.other, h.kid is a)
    if h.kid is not None:
        k = h.kid
        # a None-narrowed storage field against an alias of its payload
        print("narrowed", k is h.kid, a is h.kid)  # tpyc: ok
    if h.kid is not None:
        print("narrowed ptr", via_ptr(h, h.kid), via_ptr(h, a), kid_via_ptr(h) is h.kid)
    print("unnarrowed ptr", kid_or_none(h) is h.kid, kid_or_none_ro(h) is h.kid)
    h2 = Holder()
    print("unnarrowed ptr none", kid_or_none(h2) is None, kid_or_none_ro(h2) is None)
    o_ = Outer()
    o_.h.set_kid(3)
    hh = o_.h
    if hh.kid is not None:
        print("narrowed chain", via_chain(o_, hh.kid), via_chain(o_, a))
    print("narrowed chain method", o_.kid_is_kid(), o_.has(a), chain_value(o_))
    o: A | None = a
    print("opt local", o is a, o is h.first())  # tpyc: ok
    print("borrow return", h.first() is h.a, h.first() is a)  # tpyc: ok
    xs = [A(1), A(2)]
    # a pointer-returning call against a name
    print("call", find(xs, 2) is xs[1], find(xs, 1) is xs[1], find(xs, 9) is xs[0])  # tpyc: ok


def derived_fn() -> None:
    d = Derived()
    print("derived", as_base(d, d), as_base(Base(), d))


def same_rc(x: Rc[A], y: Rc[A]) -> bool:
    return x is y  # tpyc: ok


def rc_fn() -> None:
    r1 = Rc.new(A(1))
    r2 = r1.clone()
    r3 = Rc.new(A(1))
    # handles: a clone is another handle
    print("rc", same_rc(r1, r1), r1 is r2)  # tpyc: ok
    # payloads: clones share one
    print("rc get", r1.get() is r2.get(), r1.get() is r3.get())  # tpyc: ok
    i1 = Rc.new(10)
    i2 = i1.clone()
    print("rc int", i1 is i2)  # tpyc: ok


def positions_fn(tag: int) -> None:
    a = A(1)
    b = A(2)
    c = a
    print("gen", [v for v in gen_pos(a, b, c)])
    print("yield", [y is a for y in echo(a)])
    print("async", asyncio.run(async_pos(a, a)), asyncio.run(async_pos(a, b)))
    xs = [A(1), A(2)]
    # comprehension filter: the clause variable is the element itself
    print("comp", [x.n for x in xs if x is xs[1]])  # tpyc: ok
    t = (a, b)
    p, q = t
    print("unpack", p is a, q is a)  # tpyc: ok

    def inner() -> bool:
        return a is c and a is not b  # tpyc: ok

    # closure: captured names, one an alias of the other
    print("closure", inner())
    res = Res()
    with res as r:
        print("with", r is res)  # tpyc: ok
    try:
        same = a is c  # tpyc: ok
    finally:
        print("finally", same)
    try:
        ok = er_pos(a, a)
        print("error_return", ok)
        ok = er_pos(a, b)
        print("error_return", ok)
    except Missing:
        print("error_return missing")
    match tag:
        case 1:
            print("match", a is b)  # tpyc: ok
        case _:
            pass
    match a:
        case A() as m:
            print("capture", m is a)  # tpyc: ok
    print("global", glob() is G, glob() is H)  # tpyc: ok


def main() -> None:
    free_fn()
    method_fn()
    container_fn()
    literal_fn([1, 2], {"k": 1.0}, [3], {1.0})
    nested_fn([[1, 2], [3, 4]])
    ys = [1]
    none_pair_fn(None, ys)
    none_pair_fn(ys, ys)
    objects_fn()
    closure_narrow_fn()
    ptr_fn()
    optional_fn()
    derived_fn()
    rc_fn()
    positions_fn(1)


main()
