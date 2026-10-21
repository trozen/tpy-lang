# A yield into an owned tuple step takes the return's owning result check:
# each member it copies is warned per element, a last-use member moves, and a
# non-copyable live member is an error. diag.txt pins the warnings before it.
from typing import Iterator
from tpy import Own, int32, nocopy


class A:
    def __init__(self, v: int32) -> None:
        self.v = v


@nocopy
class N:
    def __init__(self, v: int32) -> None:
        self.v = v


def mk(p: A) -> tuple[A, int32]:
    return (p, 1)


# live local member: copied into the step
def live() -> Iterator[Own[tuple[A, int32]]]:
    a = A(1)
    yield (a, 3)  # tpyc: warning(/copies A into owned storage \(tuple element 0\)/)
    a.v = 7


# last-use member: moves, no warning
def last() -> Iterator[Own[tuple[A, int32]]]:
    a = A(1)
    yield (a, 3)


# parameter members, two reference elements
def params(p: A, q: A) -> Iterator[Own[tuple[A, A]]]:
    yield (p, q)  # tpyc: warning(/copies A into owned storage \(tuple element 0\)/) warning(/copies A into owned storage \(tuple element 1\)/)


# nullable step: the same check after the None peel
def nullable(p: A) -> Iterator[Own[tuple[A, int32]] | None]:
    yield (p, 3)  # tpyc: warning(/copies A into owned storage \(tuple element 0\)/)


# a tuple name bound to a lending call: borrowed even at its last use
def borrowed(p: A) -> Iterator[Own[tuple[A, int32]]]:
    t = mk(p)
    yield t  # tpyc: warning(/copies A into owned storage \(tuple element 0\)/)


# an Own-marked nested member keeps the slot's Own rule
def nested(a: A, b: A) -> Iterator[tuple[Own[tuple[A, A]], int32]]:
    yield ((a, b), 1)  # tpyc: warning(/\(tuple element 0\.0\)/) warning(/\(tuple element 0\.1\)/)


# a live non-copyable member cannot be copied into the step
def nocopy_live() -> Iterator[Own[tuple[N, int32]]]:
    n = N(1)
    yield (n, 3)  # tpyc: error(/cannot copy non-copyable type 'N' into owned storage \(tuple element 0\)/)
    n.v = 2
