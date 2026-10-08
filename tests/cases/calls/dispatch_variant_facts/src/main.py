# Each @dispatch variant's signature follows its own body, whatever its declaration
# order: a parameter it only reads is a const borrow (rvalues bind), one it mutates is not.
from typing import Iterator
from tpy import Own, int32, dispatch


class R:
    def __init__(self, n: int32) -> None:
        self.n = n


class Bag:
    items: list[int32]

    def __init__(self) -> None:
        self.items = [1]


def mkr() -> Own[R]:
    return R(4)


def mk() -> Own[list[float]]:
    return [1.5]


class K:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    @dispatch
    def take(self, r: R) -> None:
        self.n = r.n

    @dispatch
    def take(self, xs: list[float]) -> None:
        xs.append(2.5)
        self.n = len(xs)

    @dispatch
    def look(self, s: str) -> int32:
        return -1

    @dispatch
    def look(self, xs: list[float]) -> int32:
        return len(xs)

    @dispatch
    def look_d(self, s: str) -> int32:
        return -1

    @dispatch
    def look_d(self, d: dict[str, int32]) -> int32:
        return len(d)

    @dispatch
    def look_s(self, s: str) -> int32:
        return -1

    @dispatch
    def look_s(self, xs: set[int32]) -> int32:
        return len(xs)


def grow(xs: list[int32]) -> int32:
    xs.append(len(xs))
    return len(xs)


class Box:
    items: list[int32]

    def __init__(self) -> None:
        self.items = [0]

    @property
    def size(self) -> int32:
        return len(self.items)

    @size.setter
    def size(self, n: int32) -> None:
        grow(self.items)


def poke(b: Box) -> None:
    b.size = 3


# Free variants, the mutating one declared second.
@dispatch
def late(r: R) -> int32:
    return r.n


@dispatch
def late(xs: list[int32]) -> int32:
    xs.append(9)
    return len(xs)


# Free variants, the mutating one declared first.
@dispatch
def early(b: Bag) -> int32:
    b.items.append(5)
    return len(b.items)


@dispatch
def early(xs: list[float]) -> int32:
    return len(xs)


# Generic variants that both lend an element of their argument: a generator
# yielding either one's result lends it too, whichever is declared first.
@dispatch
def pick[T](xs: list[T], i: int32) -> T:
    return xs[i]


@dispatch
def pick[T](xs: list[T]) -> T:
    return xs[0]


def first_of[T](xs: list[T]) -> Iterator[T]:
    yield pick(xs)


def at_of[T](xs: list[T]) -> Iterator[T]:
    yield pick(xs, 0)


def main() -> None:
    k = K()
    # method: the reading variants take rvalues -- a record, a container call
    # result, a list literal and an empty list().
    k.take(mkr())  # tpyc: ok
    print("method-read:", k.n, k.look(mk()), k.look([1.5, 2.5]), k.look(list()), k.look("x"))  # tpyc: ok
    # method: an empty dict and set at read-only dict / set variants.
    print("method-empty:", k.look_d({}), k.look_s(set()), k.look_d({"a": 1}))  # tpyc: ok
    # method: the mutating variant appends to the caller's list.
    ys = [1.0]
    k.take(ys)  # tpyc: ok
    ys.append(9.0)
    print("method-mutate:", k.n, ys)
    # free, mutating variant second: the reader takes an rvalue, the writer's
    # append is seen by the caller.
    zs = [1, 2]
    print("free-late:", late(mkr()), late(zs), zs)  # tpyc: ok
    # free, mutating variant first: same, the other way round.
    bag = Bag()
    print("free-early:", early(bag), early(mk()), bag.items)  # tpyc: ok
    # generic: writing through the yielded element reaches the list's own.
    rs = [R(1)]
    for r in first_of(rs):  # tpyc: ok
        r.n = 99
    for r in at_of(rs):  # tpyc: ok
        r.n += 1
    print("generic-yield:", rs[0].n)
    # property: the setter writes the receiver's list through a callee.
    bx = Box()
    poke(bx)  # tpyc: ok
    print("setter-callee:", bx.size, bx.items)


main()
