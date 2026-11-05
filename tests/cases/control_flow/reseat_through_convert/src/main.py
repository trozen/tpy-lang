# A pointer local's reseat (`x = <src>` over an earlier `x`) aliases its new source
# at every position: each section writes through one name and reads the other.
import asyncio
from typing import Iterator
from tpy import Ptr, ReturnException, error_return, int32, readonly


class A:
    def __init__(self, n: int32) -> None:
        self.n = n


class Der(A):
    def __init__(self, n: int32) -> None:
        super().__init__(n)


class H:
    a: A
    b: A
    o: A | None
    xs: list[A]
    os: list[A | None]
    d: Der

    def __init__(self) -> None:
        self.a = A(1)
        self.b = A(2)
        self.o = A(3)
        self.xs = [A(4), A(5)]
        self.os = [A(6), None]
        self.d = Der(7)

    def der(self) -> Der:
        return self.d


# --- free function: a select of two elements, hoisted ahead of the branch
def select_hoist(h: H, c: bool, c0: bool) -> None:
    if c0:
        x = h.xs[0] if c else h.xs[1]  # tpyc: ok
    else:
        x = h.xs[1] if c else h.xs[0]
    x.n += 10
    print("select_hoist", h.xs[0].n, h.xs[1].n)


# --- free function: an Optional element of a list
def opt_elem(h: H) -> None:
    r = h.os[1]
    r = h.os[0]  # tpyc: ok
    if r is not None:
        r.n += 5
    e = h.os[0]
    print("opt_elem", e.n if e is not None else -1)
    r = h.os[1]  # tpyc: ok
    print("opt_elem_empty", r is None, e is not None)


# --- free function: a select of an Optional field or None
def opt_select(h: H, c: bool) -> None:
    t = h.o
    t = h.o if c else None  # tpyc: ok
    if t is not None:
        t.n += 3
    print("opt_select", h.o.n if h.o is not None else -1)


# --- loop: an Optional property local rebound in every iteration
class P:
    _b: A | None

    def __init__(self) -> None:
        self._b = A(2)

    @property
    def pa(self) -> A | None:
        return self._b


def prop_loop(p: P) -> None:
    t = p.pa
    for i in range(2):
        t = p.pa  # tpyc: ok
        if t is not None:
            t.n += 10
    print("prop_loop", p._b.n if p._b is not None else -1)


# --- loop: a field reseat in every iteration
def loop_field(h0: H, hs: list[H]) -> None:
    x = h0.a
    for h in hs:
        x = h.b  # tpyc: ok
        x.n += 10
    print("loop_field", hs[0].b.n, hs[1].b.n)


# --- method and constructor: the select hoisted ahead of a branch
class M:
    h: H

    def __init__(self, h: H, c: bool) -> None:
        self.h = H()
        if c:
            x = h.xs[0] if c else h.xs[1]  # tpyc: ok
        else:
            x = h.xs[1]
        x.n += 100

    def go(self, c: bool, c0: bool) -> None:
        if c0:
            x = self.h.xs[0] if c else self.h.xs[1]  # tpyc: ok
        else:
            x = self.h.xs[1]
        x.n += 10
        print("method", self.h.xs[0].n, self.h.xs[1].n)


# --- try / finally body
def try_hoist(h: H, c: bool) -> None:
    x = h.a
    try:
        x = h.xs[0] if c else h.xs[1]  # tpyc: ok
        x.n += 10
    finally:
        print("try", h.xs[0].n, h.xs[1].n)


# --- context-manager body
class Ctx:
    def __enter__(self) -> "Ctx":
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        pass


def with_hoist(h: H, c: bool) -> None:
    x = h.a
    with Ctx():
        x = h.xs[0] if c else h.xs[1]  # tpyc: ok
        x.n += 10
    print("with", h.xs[0].n, h.xs[1].n)


# --- a member of a `Ptr` receiver, mutable and readonly
def via_ptr(p: Ptr[H], q: Ptr[readonly[H]], c: bool) -> None:
    x = p.a
    x = p.xs[0] if c else p.xs[1]  # tpyc: ok
    x.n += 5
    y = q.a
    y = q.xs[0] if c else q.xs[1]  # tpyc: ok
    print("via_ptr", y.n)


# --- an @error_return borrow call, in an @error_return body
class E(Exception, ReturnException):
    pass


class EH:
    a: A
    b: A

    def __init__(self) -> None:
        self.a = A(1)
        self.b = A(2)

    @error_return(E)
    def get_b(self) -> A:
        return self.b


@error_return(E)
def err_return(h: EH) -> int32:
    x = h.a
    x = h.get_b()  # tpyc: ok
    x.n += 10
    print("err_return", h.b.n)
    return 0


# --- a union local, bound from a union parameter, reseated to a member field
class B:
    def __init__(self, n: int32) -> None:
        self.n = n


class U:
    a: A
    b: B
    xs: list[A | B]

    def __init__(self) -> None:
        self.a = A(1)
        self.b = B(2)
        self.xs = [A(3), B(4)]


def union_member(u: U, w: A | B, c: int32) -> None:
    v = w
    if c == 0:
        v = u.b  # tpyc: ok
    elif c == 1:
        v = u.a  # tpyc: ok
    if isinstance(v, B):
        v.n += 10
    elif isinstance(v, A):
        v.n += 20
    print("union_member", u.a.n, u.b.n)


# --- a borrow call handing back a subclass of the local's record
def der_call(h: H) -> None:
    x = h.a
    # The local is an `A`, so the `Der` result narrows, as at a declaration.
    x = h.der()  # tpyc: warning(/upcast narrows/)
    x.n += 5
    print("der_call", h.d.n)


# --- a dunder handing back its receiver: written through the source, read
# through the local
class S:
    def __init__(self, n: int32) -> None:
        self.n = n

    def __add__(self, n: int32) -> "S":
        return self


def dunder_self(a: S, n: int32) -> None:
    if n > 0:
        x = a + 1  # tpyc: ok
    else:
        x = a + 2  # tpyc: ok
    a.n += 7
    print("dunder_self", x.n)


# --- a tuple literal lent to a borrow call
def pick_pair(t: tuple[A, A]) -> A:
    return t[0]


def tuple_lend(a: A, b: A) -> None:
    x = b
    x = pick_pair((a, b))  # tpyc: ok
    x.n += 1
    print("tuple_lend", a.n)


# --- a narrowed storage-Optional field, in a free function, a method and
# under `assert`
def narrowed_field(h: H) -> None:
    x = h.a
    if h.o is not None:
        x = h.o  # tpyc: ok
    x.n += 1
    print("narrowed_field", h.o.n if h.o is not None else -1)


def narrowed_assert(h: H) -> None:
    x = h.a
    assert h.o is not None
    x = h.o  # tpyc: ok
    x.n += 1
    print("narrowed_assert", h.o.n)


class NH:
    o: A | None
    a: A

    def __init__(self) -> None:
        self.o = A(30)
        self.a = A(0)

    def bump(self) -> None:
        x = self.a
        if self.o is not None:
            x = self.o  # tpyc: ok
        x.n += 1
        print("narrowed_method", self.o.n if self.o is not None else -1)


# --- an engaged owned slot read as the object it holds
def engaged(n: int32) -> None:
    q: A | None = None
    b = A(n)
    for i in range(1):
        b = A(n)
        q = b  # tpyc: ok
    if q is not None:
        q.n += 1
    print("engaged", b.n)


# --- generator and async: field and Optional element reseats in frames
def gen(h: H) -> Iterator[int32]:
    x = h.a
    yield x.n
    x = h.b  # tpyc: ok
    x.n += 10
    yield x.n
    o = h.o
    yield 0 if o is None else 1
    o = h.os[0]  # tpyc: ok
    if o is not None:
        o.n += 10
        yield o.n


async def co(h: H) -> int32:
    x = h.a
    await asyncio.sleep(0)
    x = h.b  # tpyc: ok
    await asyncio.sleep(0)
    o = h.o
    await asyncio.sleep(0)
    o = h.os[0]  # tpyc: ok
    x.n += 1
    if o is not None:
        o.n += 1
    return h.b.n


# --- warned: the reseat borrows a member of a temporary the call lends back.
# Never called: the local dangles once the statement ends.
class Leaf:
    def __init__(self, n: int32) -> None:
        self.n = n


class LA:
    child: Leaf

    def __init__(self, n: int32) -> None:
        self.child = Leaf(n)


def pick(xs: tuple[LA, int32]) -> LA:
    return xs[0]


def warned_tuple_temp(a: LA) -> int32:
    x = a.child
    x = pick((LA(42), 0)).child  # tpyc: warning(/Result borrows from temporary argument/)
    return x.n


# --- warned: a loop rebind to a call lending back a temporary operand of
# `min`. Never called: the operand is hoisted into the loop body, which ends
# before the read. The bare `m = min(...)` rebind is still refused
# (BUGS.md#lend-back-of-hoisted-temp-warned-as-dangling).
class V:
    v: int

    def __init__(self, v: int) -> None:
        self.v = v


def key_of(p: V) -> int:
    return p.v


class K:
    def ret(self, p: V) -> V:
        p.v += 1
        return p


def warned_min_temp() -> int:
    a = V(5)
    k = K()
    m = a
    for i in range(2):
        m = k.ret(min(a, V(-i - 1), key=key_of))  # tpyc: warning(/Result borrows from temporary argument/)
    return m.v


def main() -> None:
    select_hoist(H(), True, True)
    select_hoist(H(), False, False)
    opt_elem(H())
    opt_select(H(), True)
    opt_select(H(), False)
    prop_loop(P())
    loop_field(H(), [H(), H()])
    h = H()
    m = M(h, True)
    print("ctor", h.xs[0].n)
    m.go(False, True)
    m.go(True, True)
    try_hoist(H(), False)
    with_hoist(H(), True)
    hp = H()
    via_ptr(hp, hp, False)
    eh = EH()
    try:
        err_return(eh)
    except E:
        print("err_return raised")
    u = U()
    union_member(u, u.xs[0], 0)
    union_member(u, u.xs[0], 1)
    union_member(u, u.xs[0], 2)
    der_call(H())
    dunder_self(S(1), 0)
    dunder_self(S(2), 1)
    tuple_lend(A(10), A(20))
    narrowed_field(H())
    narrowed_assert(H())
    NH().bump()
    engaged(3)
    hg = H()
    for v in gen(hg):
        print("gen", v)
    e = hg.os[0]
    print("gen_after", hg.b.n, e.n if e is not None else -1)
    ha = H()
    r = asyncio.run(co(ha))
    e = ha.os[0]
    print("async", r, e.n if e is not None else -1)


main()
