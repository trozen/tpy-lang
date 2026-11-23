# Members of a class at `Own[P]` that copy: a property getter, a readonly
# result, `__getitem__`, an `auto_readonly` getter (both clones), a
# `readonly[T]` step, a static method given a live local, a lambda capturing
# one; aggregates default-constructing their fields' storage form.
from __future__ import annotations
from typing import Callable, Iterator
from tpy import Own, auto_readonly, int32, readonly
from fixtures import P


class Cell[T]:
    v: T

    def __init__(self, v: T) -> None:
        self.v = v  # tpyc: warning(/may copy T into field/)

    @property
    def val(self) -> T:
        return self.v

    def peek(self) -> readonly[T]:
        return self.v

    def __getitem__(self, i: int32) -> T:
        return self.v

    @auto_readonly
    def first(self) -> auto_readonly[T]:
        return self.v

    def each(self) -> Iterator[readonly[T]]:
        yield self.v

    @staticmethod
    def make(v: T) -> Own[Cell[T]]:
        return Cell[T](v)


class Agg[T]:
    v: T


class Nest[T]:
    a: Agg[T]


class Keep[T]:
    f: Callable[[], T]

    def __init__(self, x: T) -> None:
        self.f = lambda: x  # tpyc: warning(/lambda at line 51 may keep its own copy of 'x'/)


def mkf[T](x: T) -> Callable[[], T]:
    return lambda: x  # tpyc: warning(/lambda at line 55 may keep its own copy of 'x'/)


def first_ro(ro: readonly[Cell[Own[P]]]) -> int32:
    # the const clone, through a readonly receiver: a copy as well
    r = ro.first()
    r.n += 100
    return r.n


def members() -> None:
    # every member's copy reports where the class is formed
    c = Cell[Own[P]](P(1))  # tpyc: warning(/'Cell.val' \(part_members.py:19\)/) warning(/'Cell.peek' \(part_members.py:22\)/) warning(/'Cell.__getitem__' \(part_members.py:25\)/) warning(/'Cell.first' \(part_members.py:29\)/) warning(/'Cell.each' \(part_members.py:32\)/)
    v = c.val
    v.n = 7
    k = c.peek()
    k.n = 8
    # (`i = c[0]` does not lower yet: BUGS.md#own-getitem-result-local-rejects)
    i = c.__getitem__(0)
    i.n = 9
    f = c.first()
    f.n = 10
    for e in c.each():
        e.n = 11
    print("members", c.v.n, v.n, k.n, i.n, f.n)
    print("members-ro", first_ro(c), c.v.n)


def static() -> None:
    src = P(2)
    s = Cell[Own[P]].make(src)  # tpyc: warning(/copies P into owned storage; use copy/) warning(/'Cell.val' \(part_members.py:19\)/) warning(/'Cell.peek' \(part_members.py:22\)/) warning(/'Cell.__getitem__' \(part_members.py:25\)/) warning(/'Cell.first' \(part_members.py:29\)/) warning(/'Cell.each' \(part_members.py:32\)/)
    src.n = 20
    s.v.n += 1
    print("static", s.v.n, src.n)


def agg() -> None:
    # a zero-argument class at `Own[list[int32]]` default-constructs the
    # field's storage form `list[int32]`
    a = Agg[Own[list[int32]]]()
    a.v.append(4)
    # a field's class at its arguments default-constructs as well
    n = Nest[Own[list[int32]]]()
    n.a.v.append(5)
    print("agg", len(a.v), len(n.a.v))


def lambdas() -> None:
    h = mkf[Own[P]](P(4))  # tpyc: warning(/copies P into owned storage; use copy/)
    one = h()
    one.n = 40
    print("lambda-own", one.n, h().n)
    src = P(5)
    k = Keep[Own[P]](src)  # tpyc: warning(/copies P into owned storage; use copy/) warning(/copies P into owned storage in 'Keep.__init__' \(part_members.py:51\)/)
    src.n = 50
    print("lambda-class", k.f().n, src.n)


def main() -> None:
    members()
    static()
    agg()
    lambdas()
