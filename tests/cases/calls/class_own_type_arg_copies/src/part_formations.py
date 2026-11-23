# A class at `Own[P]` formed with no spelling of its own -- a field of another
# class, a generator expression's frame, inferred static calls -- reports
# its members' copies where the program forms it; `Outer[P]` keeps aliasing.
from __future__ import annotations
from typing import Callable
from tpy import Own, Ptr, int32
from fixtures import Box, P


class Outer[T]:
    b: Box[T]

    def __init__(self, v: T) -> None:
        self.b = Box[T](v)


class Gen[T]:
    v: T
    items: list[T]
    _p: Ptr[T]

    def __init__(self, v: T) -> None:
        self.v = v  # tpyc: warning(/may copy T into field/)
        self.items = []
        self._p = Ptr()

    def one(self, x: T) -> int32:
        self.items.append(x)  # tpyc: warning(/may copy T into owned storage/)
        return 1

    def via_genexpr(self) -> int32:
        # the generator expression's frame reads the field: a copy at `Own[P]`
        return sum(self.one(self.v) for _ in range(2))

    def via_ptr(self) -> None:
        # the handle's referent handed to a `T` parameter: a copy at `Own[P]`
        self.one(self._p)


class Lot[T]:
    v: T

    def __init__(self, v: T) -> None:
        self.v = v  # tpyc: warning(/may copy T into field/)

    def get(self) -> T:
        return self.v

    @staticmethod
    def first(f: Callable[[], T], xs: list[T]) -> int32:
        return len(xs)


class Lots[T]:
    @staticmethod
    def count(f: Callable[[], T]) -> int32:
        return 1

    @staticmethod
    def pick(f: Callable[[], T], xs: list[T]) -> T:
        return xs[0]


def mk() -> Own[P]:
    return P(7)


def field_class() -> None:
    # `Box[Own[P]]` is formed as `Outer[Own[P]]`'s field
    o = Outer[Own[P]](P(1))  # tpyc: warning(/copies P into owned storage in 'Box.get' \(fixtures.py:25\)/) warning(/'Box.push_v'/) warning(/'Box.__iter__'/)
    g = o.b.get()
    g.n = 99
    print("field-class", o.b.v.n, g.n)
    # the neighbour at `P`: the field's getter lends, as CPython's
    a = Outer[P](P(2))
    h = a.b.get()
    h.n = 98
    print("field-class-plain", a.b.v.n, h.n)


def genexpr_frame() -> None:
    k = Gen[Own[P]](P(3))  # tpyc: warning(/copies P into owned storage in 'Gen.via_genexpr' \(part_formations.py:33\)/) warning(/copies P into owned storage in 'Gen.via_ptr' \(part_formations.py:37\)/)
    print("genexpr", k.via_genexpr())
    k.items[0].n = 30
    print("genexpr-copy", k.v.n, k.items[0].n, k.items[1].n)


def static_call() -> None:
    own_mk: Callable[[], Own[P]] = mk
    # the call binds `T = Own[P]`, forming `Lot[Own[P]]`
    n = Lot.first(own_mk, [P(4)])  # tpyc: warning(/copies P into owned storage in 'Lot.get' \(part_formations.py:47\)/)
    print("static-first", n)


def static_two() -> None:
    own_mk: Callable[[], Own[P]] = mk
    # two static calls on one class: the first forms `Lots[Own[P]]`, whose
    # `pick` hands back an owned copy at the second
    print("static-count", Lots.count(own_mk))  # tpyc: warning(/copies P into owned storage in 'Lots.pick' \(part_formations.py:61\)/)
    xs = [P(5)]
    q = Lots.pick(own_mk, xs)
    q.n = 50
    print("static-pick", xs[0].n, q.n)


def main() -> None:
    field_class()
    genexpr_frame()
    static_call()
    static_two()
