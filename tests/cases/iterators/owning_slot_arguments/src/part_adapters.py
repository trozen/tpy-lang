# Iteration adapters (a generator expression, copy_iter, own_iter, an
# iterator) at a protocol whose whole contract they meet: a non-owning
# `Rows[T]`, an owning `OwnRows[T]`, a non-generic `FixedRows`, a
# sub-protocol of Iterator, protocols naming no Iterable / Iterator base, a
# constructor's protocol parameter; the protocol's type argument inferred
# through the step (`Own` peeled, a tuple element). An owning protocol with
# a field takes a conforming record, lent, with the `copy()` hint.
from typing import Iterable, Iterator, Protocol
from tpy import Own, int32, copy_iter, own_iter


class R:
    def __init__(self, v: int32) -> None:
        self.v = v


class Rows[T](Iterable[T], Protocol):
    pass


class OwnRows[T](Iterable[Own[T]], Protocol):
    pass


class Pairs[T](Iterable[tuple[T, T]], Protocol):
    pass


class FixedRows(Iterable[int32], Protocol):
    pass


class Steps[T](Iterator[T], Protocol):
    pass


class Stepper[T](Protocol):
    def __next__(self) -> T: ...


class Walk(Protocol):
    def __iter__(self) -> Iterator[int32]: ...


class Tagged[T](Iterable[Own[T]], Protocol):
    tag: int32


class Bag:
    tag: int32
    items: list[R]

    def __init__(self) -> None:
        self.tag = 7
        self.items = [R(1), R(2)]

    def __iter__(self) -> Iterator[R]:
        return iter(self.items)


class Acc:
    v: int32

    def __init__(self, rs: FixedRows) -> None:
        self.v = 0
        for x in rs:
            self.v += x


def total(rs: Rows[int32]) -> int32:
    t = 0
    for x in rs:
        t += x
    return t


def take(rs: OwnRows[R]) -> int32:
    t = 0
    for r in rs:
        r.v += 100
        t += r.v
    return t


def count_own[T](rs: OwnRows[T]) -> int32:
    n = 0
    for _ in rs:
        n += 1
    return n


def count_pairs[T](rs: Pairs[T]) -> int32:
    n = 0
    for _ in rs:
        n += 1
    return n


def total_fixed(rs: FixedRows) -> int32:
    t = 0
    for x in rs:
        t += x
    return t


def sum_steps(s: Steps[int32]) -> int32:
    t = 0
    for x in s:
        t += x
    return t


def first[T](s: Stepper[T], d: T) -> T:
    try:
        return s.__next__()
    except StopIteration:
        return d


def count(it: Iterable[int32]) -> int32:
    n = 0
    for _ in it:
        n += 1
    return n


def walk(w: Walk) -> int32:
    return count(w)


def take_tagged(rs: Tagged[R]) -> int32:
    t = 0
    for r in rs:
        r.v += 10
        t += r.v
    return t


def main() -> None:
    xs = [1, 2, 3]
    # Non-owning slot: each adapter conforms by Iterable's contract.
    print("adapters rows genexpr", total(x * 2 for x in xs))  # tpyc: ok
    print("adapters rows copy_iter", total(copy_iter(xs)))  # tpyc: ok
    ys: list[int32] = [4, 5]
    print("adapters rows own_iter", total(own_iter(ys)))  # tpyc: ok
    # Owning slot: a generator expression lends `gs`'s elements, so they are
    # copied, warned (only the callee's sum is printed: CPython shares them);
    # copy_iter's are copies, so the callee's mutation does not reach `rs`.
    gs = [R(1), R(2)]
    print("adapters own genexpr", take(r for r in gs))  # tpyc: warning(/copies R elements/)
    rs = [R(1), R(2)]
    print("adapters own copy_iter", take(copy_iter(rs)), rs[0].v)  # tpyc: ok
    zs: list[R] = [R(5)]
    print("adapters own own_iter", take(own_iter(zs)))  # tpyc: ok
    # `T` inferred through the owning step and through a tuple element.
    print("adapters infer own", count_own(copy_iter(xs)))  # tpyc: ok
    ps = [(1, 2), (3, 4)]
    print("adapters infer tuple", count_pairs(copy_iter(ps)))  # tpyc: ok
    print("adapters infer tuple genexpr", count_pairs((a, a) for a in xs))  # tpyc: ok
    # A non-generic sub-protocol takes a generator expression.
    print("adapters fixed genexpr", total_fixed(x * 2 for x in xs))  # tpyc: ok
    # A sub-protocol of Iterator takes a generator expression and an iterator.
    print("adapters steps genexpr", sum_steps(x * 2 for x in xs))  # tpyc: ok
    print("adapters steps iter", sum_steps(iter(xs)))  # tpyc: ok
    # A protocol with no Iterable / Iterator base: a generator expression
    # meets `__next__` (`T` inferred), copy_iter meets `__iter__`.
    print("adapters stepper genexpr", first((x * 3 for x in xs), 0))  # tpyc: ok
    print("adapters walk copy_iter", walk(copy_iter(xs)))  # tpyc: ok
    # A constructor's protocol parameter takes a generator expression.
    print("adapters ctor genexpr", Acc(x for x in xs).v)  # tpyc: ok
    # An owning protocol with a field: copy_iter does not meet it, so the
    # lent record's element copy names copy(), the record live after the call
    # or not.
    b = Bag()
    print("adapters tagged live", take_tagged(b), b.tag)  # tpyc: warning(/copies R elements; use copy\(\) to make this explicit/)
    d = Bag()
    print("adapters tagged dead", take_tagged(d))  # tpyc: warning(/copies R elements; use copy\(\) to make this explicit/)
