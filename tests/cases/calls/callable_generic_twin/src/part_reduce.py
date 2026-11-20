# `functools.reduce` over owning and lending combiners. When the lending
# and the building variant otherwise tie, the one whose callable parameter
# writes `Own[T]` where the combiner returns an owned value fits better
# than a bare `T` bound to `Own[P]`; a better fit elsewhere still decides.
from functools import reduce
from tpy import Fn, Own, ReferenceType, int32


class P:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


def own_add2(a: Own[P], b: Own[P]) -> Own[P]:
    return P(a.x + b.x)


def own_acc(a: Own[P], b: P) -> Own[P]:
    a.x += b.x
    return a


def add(a: int32, b: int32) -> int32:
    return a + b


def bigger(a: P, b: P) -> P:
    return a if a.x >= b.x else b


def add_p(a: P, b: P) -> Own[P]:
    return P(a.x + b.x)


def red_b[T, U: ReferenceType](func: Fn[[U, T], U], a: list[T],
                               initial: U) -> U:
    acc = initial
    for i in range(len(a)):
        acc = func(acc, a[i])
    return acc


def red_u[T, U](func: Fn[[U, T], U], a: list[T], initial: U) -> U:
    acc = initial
    for i in range(len(a)):
        acc = func(acc, a[i])
    return acc


def main() -> None:
    # tie broken by the owned result: the building 2-arg variant; the
    # warned copy is of the list's elements into `own_add2`'s owned
    # parameters (`func(a[0], a[1])` leaves them in the list)
    r = reduce(own_add2, [P(1), P(2), P(3)])  # tpyc: warning(/copies P into owned storage/)
    print("reduce-own2", r.x)
    ps = [P(1), P(2), P(3)]
    # `list` beats `Iterable`: the lending 3-arg variant at `U = Own[P]`,
    # whose owned result borrows nothing of the temporary `P(10)`; the
    # warned copy is of the accumulator into `own_acc`'s owned parameter
    # at each step (`func(acc, a[i])` is no last use of `acc` in the stub)
    r2 = reduce(own_acc, ps, P(10))  # tpyc: warning(/copies P into owned storage/)
    print("reduce-acc-temp", r2.x)
    # no owned result: the picks the bounds make are unchanged
    print("reduce-add", reduce(add, [1, 2, 3]))  # tpyc: ok
    qs = [P(1), P(2)]
    reduce(bigger, qs).x += 100  # tpyc: ok
    print("reduce-lend", qs[1].x)
    s = reduce(add_p, qs)  # tpyc: ok
    print("reduce-build", s.x, qs[0].x)
    # a user generic, bounded and unbounded alike: at `U = Own[P]` the
    # result is owned and borrows nothing of the temporary `P(10)`; the
    # warned copy is the accumulator's at each step, as in `reduce`
    rb = red_b(own_acc, ps, P(10))  # tpyc: warning(/copies P into owned storage/)
    print("red-bounded", rb.x)
    ru = red_u(own_acc, ps, P(10))  # tpyc: warning(/copies P into owned storage/)
    print("red-unbounded", ru.x)
