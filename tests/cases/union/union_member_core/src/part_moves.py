# What a union member value or a tuple element hands on is decided once,
# as its scalar twin's is: each section sits beside its twin and prints
# what CPython prints for both. (A tuple of a value union and a reference
# has no reader yet, BUGS.md#value-union-tuple-element-reads-reject: those
# bodies' C++ is the subject, built by the exec phase.)
from tpy import Own, ValueType, int32, copy, nocopy


class Base:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class A:
    xs: list[int32]

    def __init__(self, n: int32) -> None:
        self.xs = [n]


class B:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class V(ValueType):
    s: str

    def __init__(self, s: str) -> None:
        self.s = s


# narrowed: a narrowed member of a const union parameter is lent to a
# pointer-variant parameter by its alias's address (`&(__u)`)
def ptr_union(x: Base | V) -> str:
    if isinstance(x, Base):
        return "base"
    return x.s


def lend_narrowed(u: V | str) -> str:
    if isinstance(u, V):
        return ptr_union(u)  # tpyc: ok
    return "none"


# narrowed: an `Own` parameter's narrowed member returned as itself or in
# a union is refused, never copied unwarned: the narrowed read is the
# member's alias, which no move takes the parameter out of
# (tests/cases/union/error_narrowed_own_member_return,
# tests/cases/union/error_narrowed_own_union_return)


# narrowed: a nested def returning a captured local's narrowed member
# copies it -- the alias is a reference no `return` moves -- so the
# enclosing local reads the same value on a second call and after
def captured_narrowed(k: int32) -> None:
    u: str | int32 = "c" + str(k)

    def inner() -> str | int32:
        if isinstance(u, str):
            return u  # tpyc: ok
        return 0
    r = inner()
    r2 = inner()
    if isinstance(r, str) and isinstance(r2, str) and isinstance(u, str):
        print("moves-captured", r, r2, u)


# narrowed: the reference twin's captured member is copied too; nothing
# writes it after the return, so the output does not show alias-vs-copy
def captured_narrowed_rec(k: int32) -> None:
    u: A | int32 = A(k)

    def inner() -> Own[A | int32]:
        if isinstance(u, A):
            # copy is warned: CPython aliases
            return u  # tpyc: warning(/copies A/)
        return 0
    r = inner()
    if isinstance(r, A):
        print("moves-captured-rec", len(r.xs))
    if isinstance(u, A):
        print("moves-captured-rec", len(u.xs))


# narrowed: a narrowed member returned past a `finally` is copied before
# the `finally` reads it
def narrowed_finally(k: int32) -> V | int32:
    u: V | str = V("f" + str(k))
    if isinstance(u, V):
        try:
            return u  # tpyc: ok
        finally:
            print("moves-narrowed-finally", u.s)
    return 0


# narrowed: the reference twin past a `finally` is copied before the
# `finally` reads it; the `finally` only reads, so the output does not
# show alias-vs-copy
def narrowed_finally_rec(k: int32) -> Own[A | int32]:
    u: A | str = A(k)
    if isinstance(u, A):
        try:
            # copy is warned: CPython aliases
            return u  # tpyc: warning(/copies A/)
        finally:
            print("moves-narrowed-finally-rec", len(u.xs))
    return 0


@nocopy
class NC:
    xs: list[int32]

    def __init__(self, n: int32) -> None:
        self.xs = [n]


# narrowed: a @nocopy member's narrowed alias handed back at a borrowed
# result is the caller's object, neither moved nor copied
def nc_first(u: NC | int32, d: NC) -> NC:
    if isinstance(u, NC):
        return u  # tpyc: ok
    return d


# tuple at a borrowing result: the str element the result owns is copied
# out of the dying tuple where the twin's `return t;` moves the tuple
# (BUGS.md#dying-owned-value-staged-result-copies; a record element there,
# `tuple[Own[A | int32], B]`, is refused as an undeclared copy:
# tests/cases/union/error_union_tuple_mixed_return); the borrowed one
# stays the caller's
def str_mixed(s: Own[str], b: B) -> tuple[str | int32, B]:
    t = (s, b)
    return t  # tpyc: ok


def str_mixed_twin(s: Own[str], b: B) -> tuple[str, B]:
    t = (s, b)
    return t  # tpyc: ok


def rec_mixed_twin(b: B) -> tuple[Own[A], B]:
    t = (A(1), b)
    return t  # tpyc: ok


# tuple copies: a record element a closure still reads is copied (warned),
# as the twin copies the tuple; a str element is copied unwarned (a value)
def rec_closure(k: int32) -> tuple[Own[A | int32], int32]:
    t = (A(k), k)

    def f() -> int32:
        return len(t[0].xs)
    print("moves-closure", f())
    return t  # tpyc: warning(/copies/)


def rec_closure_twin(k: int32) -> tuple[Own[A], int32]:
    t = (A(k), k)

    def f() -> int32:
        return len(t[0].xs)
    print("moves-closure", f())
    return t  # tpyc: warning(/copies/)


def str_closure(k: int32) -> tuple[str | int32, int32]:
    t = ("s" + str(k), k)

    def f() -> int32:
        return len(t[0])
    print("moves-closure", f())
    return t  # tpyc: ok


# tuple copies: a str element a `finally` reads after the return is
# copied, as the twin's staged result copies the tuple
def str_finally(k: int32) -> tuple[str | int32, int32]:
    t = ("f" + str(k), k)
    try:
        return t  # tpyc: ok
    finally:
        print("moves-finally", t[0])


def str_finally_twin(k: int32) -> tuple[str, int32]:
    t = ("f" + str(k), k)
    try:
        return t  # tpyc: ok
    finally:
        print("moves-finally", t[0])


# tuple copies: `copy(t)` is the one copy; its elements move out of it
def rec_copied(k: int32) -> tuple[Own[A | int32], int32]:
    t = (A(k), k)
    return copy(t)  # tpyc: ok


def rec_copied_twin(k: int32) -> tuple[Own[A], int32]:
    t = (A(k), k)
    return copy(t)  # tpyc: ok


class CBase:
    n: int32

    def __init__(self, src: "CSub") -> None:
        print("ran-base-init", src.n)
        self.n = src.n + 1000


class CSub(CBase):
    # its base's only constructor takes a CSub: nothing calls it
    def __init__(self, n: int32) -> None:  # tpyc: warning(/does not call/)
        self.n = n


# member-init and field write: a tuple member moves in element by element,
# each at its own slot -- the subclass element hands over its base part,
# never running `CBase.__init__(src: CSub)` -- as the twin's tuple field
# takes it
class Relocate:
    u: tuple[CBase, int32] | int32
    t: tuple[CBase, int32]

    def __init__(self, s1: Own[CSub], s2: Own[CSub]) -> None:
        t1 = (s1, 0)
        t2 = (s2, 0)
        # the base element keeps only the base part, in both
        self.u = t1  # tpyc: warning(/upcast narrows/)
        self.t = t2  # tpyc: warning(/upcast narrows/)

    def put(self, s: Own[CSub]) -> None:
        t = (s, 2)
        # the base element keeps only the base part, as the twin's does
        self.u = t  # tpyc: warning(/upcast narrows/)

    def put_twin(self, s: Own[CSub]) -> None:
        t = (s, 2)
        # the base element keeps only the base part
        self.t = t  # tpyc: warning(/upcast narrows/)


def show(tag: str, u: A | int32, n: int32) -> None:
    if isinstance(u, A):
        print(tag, len(u.xs), n)


def main() -> None:
    print("moves-narrowed", lend_narrowed(V("v")))
    captured_narrowed(3)
    nf = narrowed_finally(4)
    if isinstance(nf, V):
        print("moves-narrowed-finally", nf.s)
    captured_narrowed_rec(2)
    nfr = narrowed_finally_rec(5)
    if isinstance(nfr, A):
        print("moves-narrowed-finally-rec", nfr.xs[0])
    na = NC(1)
    nd = NC(0)
    nu: NC | int32 = na
    nr = nc_first(nu, nd)
    # the result is the caller's object: the write shows through `na`
    nr.xs.append(2)
    print("moves-nocopy", len(na.xs))
    b = B(2)
    s2, sb2 = str_mixed_twin("m", b)
    print("moves-mixed", s2, sb2.n)
    a, ab = rec_mixed_twin(b)
    # the borrowed element is the caller's object, written after the
    # boundary
    ab.n = 7
    print("moves-mixed-rec", len(a.xs), b.n)
    c, n = rec_closure(3)
    show("moves-closure", c, n)
    c2, n2 = rec_closure_twin(3)
    print("moves-closure", len(c2.xs), n2)
    f2, fk2 = str_finally_twin(6)
    print("moves-finally", f2, fk2)
    p, pk = rec_copied(8)
    show("moves-copy", p, pk)
    p2, pk2 = rec_copied_twin(8)
    print("moves-copy", len(p2.xs), pk2)
    h = Relocate(CSub(1), CSub(1))
    h.put(CSub(5))
    h.put_twin(CSub(5))
    t = h.t
    print("moves-relocate", t[0].n)
