# Tuple leaves an owning slot copies where CPython aliases are warned per
# leaf: a captured tuple returned by a closure, and an open `T` leaf (hedged).
from tpy import int32, Own
from typing import Callable


class A:
    def __init__(self, v: int32) -> None:
        self.v = v


# nested def returning a captured local tuple (the copy() spelling that
# silences it: tuple/own_slot_tuple_element_copies, captured_remedy)
def s1a() -> None:
    t = (A(1), 3)

    def inner() -> tuple[Own[A], int32]:
        return t  # tpyc: warning(/copies A into owned storage \(tuple element 0\)/)

    r = inner()
    t[0].v = 10
    print("s1a", r[0].v, t[0].v)


# nested def returning a captured local tuple at a whole Own[tuple] result
def s1b() -> None:
    t = (A(1), 3)

    def inner() -> Own[tuple[A, int32]]:
        return t  # tpyc: warning(/copies A into owned storage \(tuple element 0\)/)

    r = inner()
    t[0].v = 10
    print("s1b", r[0].v, t[0].v)


# lambda returning a captured local tuple (it also copies the capture when
# created, which the capture-copy warning names)
def s1c() -> None:
    t = (A(1), 3)
    f: Callable[[], Own[tuple[A, int32]]] = lambda: t  # tpyc: warning(/copies A into owned storage \(tuple element 0\)/) warning(/may keep its own copy of 't'/)
    r = f()
    t[0].v = 10
    print("s1c", r[0].v, t[0].v)


# nested def returning a captured parameter whose element is declared Own;
# the closure copies rather than moves it, so nothing consumes `t`
def s1d(t: tuple[Own[A], int32]) -> None:  # tpyc: warning(/owned tuple param 't' is never consumed/)
    def inner() -> tuple[Own[A], int32]:
        return t  # tpyc: warning(/copies A into owned storage \(tuple element 0\)/)

    r = inner()
    t[0].v = 20
    print("s1d", r[0].v, t[0].v)


# nested def returning a captured local that borrows one leaf and owns the
# other: both copy into the owned result
def s1e(a: A) -> None:
    t = (a, A(2))

    def inner() -> tuple[Own[A], Own[A]]:
        return t  # tpyc: warning(/copies A into owned storage \(tuple element 0\)/) warning(/copies A into owned storage \(tuple element 1\)/)

    r = inner()
    t[0].v = 30
    t[1].v = 40
    print("s1e", r[0].v, r[1].v, t[0].v, t[1].v)


# nested def returning a captured borrowed parameter at an Own[tuple] result
def s1f(t: tuple[A, int32]) -> None:
    def inner() -> Own[tuple[A, int32]]:
        return t  # tpyc: warning(/copies A into owned storage \(tuple element 0\)/)

    r = inner()
    t[0].v = 50
    print("s1f", r[0].v, t[0].v)


# generic subscript appended: the open `T` leaf is hedged
def s2d[T](ps: list[tuple[T, int32]], out: list[tuple[T, int32]]) -> None:
    out.append(ps[0])  # tpyc: warning(/may copy T into owned storage \(tuple element 0\) if not a value type/)


def main() -> None:
    s1a()
    s1b()
    s1c()
    s1d((A(5), 6))
    s1e(A(1))
    s1f((A(1), 2))
    ps = [(A(1), 2)]
    out: list[tuple[A, int32]] = []
    s2d(ps, out)
    out[0][0].v = 7
    print("s2d", ps[0][0].v, out[0][0].v)


main()
