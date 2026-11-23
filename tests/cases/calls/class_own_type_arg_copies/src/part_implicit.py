# A member a node calls without spelling it -- a property, a subscript, an
# operator, `len`, a `for`, `del` / item assignment, `with`, a truth test --
# copies at `Own[P]` as the named call does, each copy reported; an owning
# local hands its value on at its last use unless a later binding takes the
# ownership back; a `T` parameter's closures.
from __future__ import annotations
from typing import Callable, Iterator, Protocol
from tpy import Own, ValueType, int32, readonly
from fixtures import P


class Cup[T]:
    v: T

    def __init__(self, v: T) -> None:
        self.v = v  # tpyc: warning(/may copy T into field/)

    @property
    def val(self) -> T:
        return self.v

    def __getitem__(self, i: int32) -> T:
        return self.v

    def __add__(self, k: int32) -> T:
        return self.v

    def __len__(self) -> int32:
        return 1

    def __iter__(self) -> Iterator[T]:
        yield self.v


def peek[T](x: readonly[T]) -> bool:
    return True


class Jar[T]:
    v: T
    items: list[T]

    def __init__(self, v: T) -> None:
        self.v = v  # tpyc: warning(/may copy T into field/)
        self.items = []

    def put(self, x: T) -> None:
        self.items.append(x)  # tpyc: warning(/may copy T into owned storage/)

    # each hands the stored value to an owning parameter at `Own[P]`
    def __setitem__(self, i: int32, x: T) -> None:
        self.put(self.v)

    def __delitem__(self, i: int32) -> None:
        self.put(self.v)

    def __enter__(self) -> T:
        return self.v

    def __exit__(self, et, ev, tb) -> None:
        pass

    def __bool__(self) -> bool:
        return peek[T](self.v)


class Tin[T]:
    v: T

    def __init__(self, v: T) -> None:
        self.v = v  # tpyc: warning(/may copy T into field/)

    def __len__(self) -> int32:
        return 1 if peek[T](self.v) else 0


class Hold[T]:
    f: Callable[[], Own[Cup[T]]]

    def __init__(self, f: Callable[[], Own[Cup[T]]]) -> None:
        self.f = f


class Sink[T]:
    v: T
    items: list[T]

    def __init__(self, v: Own[T]) -> None:
        self.v = v
        self.items = []

    def put(self, x: T) -> None:
        self.items.append(x)  # tpyc: warning(/may copy T into owned storage/)

    def put_last(self, f: Callable[[], Own[T]]) -> None:
        # the owning local's last use hands its value on at `Own[P]`
        loc = f()
        self.put(loc)  # tpyc: ok

    def put_loop(self, f: Callable[[], Own[T]]) -> None:
        # rebound to the field after the first pass: the read copies
        loc = f()
        for _ in range(2):
            self.put(loc)
            loc = self.v


class HasN(Protocol):
    n: int32


def two[T: HasN](x: T) -> tuple[Callable[[], int32], Callable[[], int32]]:
    # at `Own[P]` each closure holds a copy of its own, as the twin's
    def g() -> int32:
        return x.n
    def h() -> int32:
        return x.n
    return (g, h)


def rebound[T: ValueType](x: T, y: T) -> Callable[[], Own[T]]:
    # the closure takes the parameter over; the rebinding is not seen
    def inner() -> Own[T]:  # tpyc: warning(/reassigned after the closure is created/)
        return x
    x = y
    return inner


def bump(p: P) -> int32:
    p.n += 10
    return p.n


def kept[T: ValueType](x: T) -> Callable[[], Own[T]]:
    # nothing rebinds the parameter: the closure's value is the only one
    def inner() -> Own[T]:  # tpyc: ok
        return x
    return inner


def mkcup() -> Own[Cup[Own[P]]]:  # tpyc: warning(/'Cup.val'/) warning(/'Cup.__getitem__'/)
    return Cup[Own[P]](P(1))  # tpyc: warning(/'Cup.val'/) warning(/'Cup.__getitem__'/)


def implicit(h: Hold[Own[P]]) -> None:  # tpyc: warning(/copies P into owned storage in 'Cup.val' \(part_implicit.py:20\)/) warning(/'Cup.__getitem__'/) warning(/'Cup.__add__'/) warning(/'Cup.__iter__'/)
    c = h.f()
    # each call hands back an owned copy: the cup keeps its value
    print("implicit", bump(c.val), bump(c[0]), bump(c + 1), c.v.n, len(c))
    for e in c:
        e.n = 44
    print("implicit-iter", c.v.n)


def mkjar() -> Own[Jar[Own[P]]]:  # tpyc: warning(/'Jar.__setitem__'/) warning(/'Jar.__delitem__'/) warning(/'Jar.__enter__'/) warning(/'Jar.__bool__'/)
    return Jar[Own[P]](P(1))  # tpyc: warning(/'Jar.__setitem__'/)


def dunders(mk: Callable[[], Own[Jar[Own[P]]]]) -> None:  # tpyc: warning(/'Jar.__setitem__' \(part_implicit.py:52\)/) warning(/'Jar.__delitem__' \(part_implicit.py:55\)/) warning(/'Jar.__enter__' \(part_implicit.py:58\)/) warning(/'Jar.__bool__' \(part_implicit.py:64\)/)
    j = mk()
    # item assignment and `del` each store a copy of the jar's value
    j[0] = P(3)
    del j[0]
    j.items[0].n = 10
    print("dunder-set", j.v.n, j.items[0].n, len(j.items))
    with j:
        print("dunder-with", j.v.n)
    if j:
        print("dunder-bool", len(j.items))
    # a truth test through `__len__`
    t = Tin[Own[P]](P(2))  # tpyc: warning(/copies P into owned storage in 'Tin.__len__' \(part_implicit.py:74\)/)
    if t:
        print("dunder-len", t.v.n)


def owned_local() -> None:
    s = Sink[Own[P]](P(5))  # tpyc: warning(/copies P into owned storage in 'Sink.put_loop' \(part_implicit.py:104\)/)
    s.put_last(lambda: P(6))
    s.items[0].n = 60
    print("owned-last", s.items[0].n)
    s.put_loop(lambda: P(7))
    s.items[2].n = 99
    print("owned-loop", s.v.n, s.items[2].n)


def captures() -> None:
    g, h = two[Own[P]](P(8))  # tpyc: warning(/copies P into escaping closure 'g'/) warning(/copies P into escaping closure 'h'/)
    print("captures", g(), h())
    # at `P` both closures hold the caller's object
    q = P(9)
    g2, h2 = two(q)
    q.n = 90
    print("captures-plain", g2(), h2())
    print("rebound", rebound(1, 2)(), kept(3)())


def main() -> None:
    implicit(Hold[Own[P]](mkcup))  # tpyc: warning(/'Cup.val'/) warning(/'Cup.__getitem__'/) warning(/'Cup.__add__'/) warning(/'Cup.__iter__'/)
    dunders(mkjar)
    owned_local()
    captures()
