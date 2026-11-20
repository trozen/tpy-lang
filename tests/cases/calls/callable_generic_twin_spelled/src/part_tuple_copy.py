# A tuple literal's member at an open-`T` element at `T = P` takes its
# scalar's rule: it lends, so a write after the boundary reaches the
# caller's object. At `T = Own[P]` a tuple holding `T` is refused
# (calls/error_own_type_arg_composite_leaf*); a plain `Own[int32]` is
# `int32` and admitted. No `T = str` section: a `tuple[T, int32]` result at
# `str` fails the C++ build (BUGS.md#generic-tuple-str-result-in-coroutine).
# An `enumerate` step that lends a stored element is admitted at a spelled
# `T = Own[P]`; a lending `d.get` result is refused
# (BUGS.md#own-type-arg-composite-leaf-refused).
from typing import Protocol
from tpy import Own, int32


class Grow(Protocol):
    def grow(self, v: int32) -> int32: ...


class P:
    xs: list[int32]

    def __init__(self, n: int32) -> None:
        self.xs = [n]

    def grow(self, v: int32) -> int32:
        self.xs.append(v)
        return len(self.xs)


def pair[T](x: T, out: list[T]) -> tuple[T, int32]:
    # A second sink beside the tuple member: the stored copy is hedged at the
    # declaration, while the member lends at `T = P` (`len(out)` shows both ran).
    out.append(x)  # tpyc: warning(/may copy T into owned storage/)
    return (x, 1)


# both elements lend the same object
def two[T](x: T) -> tuple[T, T]:
    return (x, x)


def second[T](t: tuple[T, int32]) -> int32:
    return t[1]


def grow_all[T: Grow](xs: list[T]) -> int32:
    c = 0
    # each `tuple[int32, T]` step lends the stored element
    for i, v in enumerate(xs):  # tpyc: ok
        c += v.grow(i)
    return c


# A generic class holding a tuple of its `T`; only its constructor is called
# here: calling `pair()` fails the C++ build
# (BUGS.md#generic-method-tuple-result-concrete-rejects).
class Box[T]:
    v: T

    def __init__(self, v: T) -> None:
        # the parameter is borrowed, so the field takes a copy of it
        self.v = v  # tpyc: warning(/may copy T into field/)

    def pair(self) -> tuple[T, int32]:
        return (self.v, 1)


def main() -> None:
    p = P(1)
    out: list[P] = []
    t = pair[P](p, out)  # tpyc: ok
    t[0].xs.append(2)
    print("tuple-pair-lend", p.xs, t[1], len(out))
    q = P(7)
    w = two[P](q)  # tpyc: ok
    w[0].xs.append(8)
    print("tuple-two-lend", q.xs, w[1].xs)
    print("tuple-plain-own", second[Own[int32]]((3, 1)))  # tpyc: ok
    # the class at a plain `T` (no `Own[X]` class type argument until unit
    # 2b: BUGS.md#class-own-type-arg-method-result-ill-formed)
    b = Box[P](P(4))  # tpyc: ok
    b.v.xs.append(5)
    print("tuple-class-own", len(b.v.xs))
    ps = [P(30), P(40)]
    print("tuple-enum-lend", grow_all[Own[P]](ps))  # tpyc: ok
    print("tuple-enum-after", ps[0].xs, ps[1].xs)
