# Parameters passed in a by-value slot (the trait's `T | None`, a pointer-form
# Optional, a pointer-variant union) copy the slot; value Optionals / unions
# copy as values; a recursive union and a protocol union stay references.
from typing import Callable, Protocol, runtime_checkable
from tpy import Own, int32

from fixtures import C, V, make_str

type Tree = int | list[Tree]


@runtime_checkable
class P1(Protocol):
    def get(self) -> int32: ...


@runtime_checkable
class P2(Protocol):
    def size(self) -> int32: ...


def mko[T](x: T | None) -> Callable[[], None]:
    def g() -> None:  # tpyc: ok
        print("opt.T", x)  # the trait's slot is copied, not referenced
    return g


def mkc(x: C | None) -> Callable[[], None]:
    def g() -> None:  # tpyc: ok
        print("opt.C", x)  # a pointer passed by value: the closure copies the pointer
    return g


def mku(x: C | V) -> Callable[[], None]:
    def g() -> None:  # tpyc: ok
        print("union", x)  # a pointer variant passed by value: copied as the pointer
    return g


def mkb(x: int | None) -> Callable[[], None]:
    # A value Optional holding a BigInt: the closure copies the number,
    # unobservably (BUGS.md#immutable-value-capture-cost-warning).
    def g() -> None:  # tpyc: warning(/nested function 'g' copies 'x' when it is created/)
        print("opt.int", x)
    return g


def mkis(x: int32 | str) -> Callable[[], None]:
    # A value union holding a str: the closure copies the string, likewise.
    def g() -> None:  # tpyc: warning(/nested function 'g' copies 'x' when it is created/)
        print("union.val", x)
    return g


def mkoc(x: Own[C] | None) -> Callable[[], None]:
    # The parameter owns its C (`std::optional<C>`), so the closure copies the
    # object (BUGS.md#own-optional-capture-copies-at-last-use).
    def g() -> None:  # tpyc: warning(/copies the object 'x' refers to/)
        print("opt.own", x is None)
    return g


def mkpo(x: P1 | None) -> Callable[[], None]:
    def g() -> None:  # tpyc: ok
        if x is not None:
            print("opt.P", x.get())  # a nullable protocol is a pointer passed by value
    return g


def mkpq(x: P1 | P2 | None) -> Callable[[], None]:
    def g() -> None:  # tpyc: ok
        if isinstance(x, P1):
            print("opt.PQ", x.get())  # likewise a nullable protocol union
    return g


def total(t: Tree) -> int:
    if isinstance(t, int):
        return t
    s = 0
    for c in t:
        s += total(c)
    return s


def mkt(x: Tree) -> Callable[[], None]:
    def g() -> None:  # tpyc: ok
        print("tree", total(x))  # a recursive union: the caller's object
    return g


class A:
    def __init__(self, n: int32) -> None:
        self.n = n

    def get(self) -> int32:
        return self.n


def mkp(x: P1 | P2) -> Callable[[], None]:
    def g() -> None:  # tpyc: ok
        if isinstance(x, P1):
            print("proto", x.get())  # a protocol union: the caller's object
    return g


def main() -> None:
    fo = mko(make_str(10))
    junk = make_str(11)
    fo()
    co = C(5)
    ho = mko(co)
    co.n = 6
    ho()
    nc: C | None = None  # typed, so T is C
    fn = mko(nc)
    fn()
    kc = mkc(co)
    co.n = 7
    kc()
    ku = mku(co)
    co.n = 8
    ku()
    mkb(10 ** 30)()
    mkis(3)()
    mkoc(C(1))()
    t: Tree = [1, 2, 3]
    ft = mkt(t)
    if isinstance(t, list):
        t.append(3)
    ft()
    a = A(1)
    fp = mkp(a)
    fpo = mkpo(a)
    fpq = mkpq(a)
    a.n = 2
    fp()
    fpo()
    fpq()
    print("junk", len(junk))
