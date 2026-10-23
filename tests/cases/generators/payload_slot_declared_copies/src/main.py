# Yields whose value is a borrowed or still-live object at an owned slot: each
# takes the warned copy into owned storage, as the sync return does. The
# mutation after the boundary shows the copy.
from typing import Iterator
from tpy import Own, int32


class A:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class B:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class H:
    t: tuple[A, int32]

    def __init__(self, a: Own[A]) -> None:
        self.t = (a, 5)


class Holds:
    a: A

    def __init__(self) -> None:
        self.a = A(42)

    def borrow(self) -> A:
        return self.a


def borrow_free(h: Holds) -> A:
    return h.a


def make_mixed(n: int32, b: A) -> tuple[Own[A], A]:
    return (A(n), b)


def mk(p: A) -> tuple[A, int32]:
    return (p, 3)


def param_lit(p: A) -> Iterator[Own[tuple[A, int32]]]:
    # the parameter is copied into the owned tuple
    yield (p, 3)  # tpyc: warning(/copies A into owned storage \(tuple element 0\)/)


def borrowed_name(p: A) -> Iterator[Own[tuple[A, int32]]]:
    t = mk(p)
    # a name bound to existing storage
    yield t  # tpyc: warning(/copies A into owned storage \(tuple element 0\).*'t' is bound to existing storage/)


def lending_call(p: A) -> Iterator[Own[tuple[A, int32]]]:
    # a call lending its argument
    yield mk(p)  # tpyc: warning(/copies A into owned storage \(tuple element 0\)/)


def element(ts: list[tuple[A, int32]]) -> Iterator[Own[tuple[A, int32]]]:
    # a container element
    yield ts[0]  # tpyc: warning(/copies A into owned storage \(tuple element 0\)/)


def field(h: H) -> Iterator[Own[tuple[A, int32]]]:
    # a field
    yield h.t  # tpyc: warning(/copies A into owned storage \(tuple element 0\)/)


def live(a: A) -> Iterator[Own[tuple[A, int32]]]:
    u = (A(2), 3)
    # the name is read again after the yield
    yield u  # tpyc: warning(/copies A into owned storage \(tuple element 0\)/)
    print("live after", u[0].v)


def borrow_calls(h: Holds) -> Iterator[Own[A]]:
    # a borrow-returning call at an owned slot: free and method spellings
    yield borrow_free(h)  # tpyc: warning(/copies A into owned storage/)
    yield h.borrow()  # tpyc: warning(/copies A into owned storage/)


def genexpr_rebind(v: A) -> None:
    p = make_mixed(1, v)
    p = make_mixed(2, v)
    # the generator expression's capture copies the rebound tuple
    for t in (p for i in range(2)):  # tpyc: warning(/copies A into owned storage \(tuple element 0\)/)
        t[0].v += 10
    print("genexpr_rebind", p[0].v)


def to_name(p: A) -> Iterator[tuple[Own[A], int32]]:
    t = (p, 1)
    # a borrowed tuple name at a per-element Own slot
    yield t  # tpyc: warning(/copies A into owned storage \(tuple element 0\)/)


def to_call(p: A) -> Iterator[tuple[Own[A], int32]]:
    # a lending call at a per-element Own slot
    yield mk(p)  # tpyc: warning(/copies A into owned storage \(tuple element 0\)/)


def mixed_fin_yield(b: B) -> Iterator[tuple[Own[A], B]]:
    t = (A(1), b)
    try:
        # the mixed tuple yielded while the finally still reads it: copied
        yield t  # tpyc: warning(/copies A into owned storage \(tuple element 0\)/)
    finally:
        t[0].v = 99


def g_mixed[T](p: T, q: A) -> tuple[Own[T], A]:
    t = (p, q)
    # a generic tuple local returned at a mixed slot: the open T leaf it
    # holds by value is a hedged copy, as its scalar twin's is
    return t  # tpyc: warning(/may copy T into owned storage \(tuple element 0\) if not a value type/)


def main() -> None:
    p = A(1)
    for q in param_lit(p):
        q[0].v = 9
    print("param_lit", p.v)
    for q in borrowed_name(p):
        q[0].v = 9
    print("borrowed_name", p.v)
    for q in lending_call(p):
        q[0].v = 9
    print("lending_call", p.v)
    ts = [(A(1), 1)]
    for q in element(ts):
        q[0].v = 9
    print("element", ts[0][0].v)
    h = H(A(1))
    for q in field(h):
        q[0].v = 9
    print("field", h.t[0].v)
    for q in live(p):
        q[0].v = 9
        print("live", q[0].v)
    hs = Holds()
    for ba in borrow_calls(hs):
        ba.v = 9
    print("borrow_calls", hs.a.v)
    genexpr_rebind(A(8))
    for q in to_name(p):
        q[0].v = 9
    print("to_name", p.v)
    for q in to_call(p):
        q[0].v = 9
    print("to_call", p.v)
    gm = g_mixed(p, p)
    gm[0].v = 7
    print("g_mixed", p.v)
    b = B(2)
    for mq in mixed_fin_yield(b):
        mq[0].v = 5
        print("mixed_fin_yield", mq[0].v)


main()
