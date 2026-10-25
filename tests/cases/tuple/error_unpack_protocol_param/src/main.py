# An unpacking for head over a protocol parameter is refused, even one whose
# steps are declared `Own`: a lent list's iterator is accepted for it and its
# step copies the stored tuple, so `a` would not refer to the list's A
# (BUGS.md#combinator-tuple-step-copies-record-element,
# BUGS.md#lent-list-at-own-iterable-param-copies).
from typing import Iterator
from tpy import Own, int32


class A:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


def f(it: Iterator[tuple[Own[A], int32]]) -> None:
    keep: list[A] = []
    # the refused head
    for a, n in it:  # tpyc: error(/not yet supported by C\+\+ code generation \(stmt\.for_each:iter\.user_iterator\.name\)/)
        a.v += n
        keep.append(a)
    print(keep[0].v)


def h(xs: list[tuple[Own[A], int32]]) -> None:
    f(iter(xs))
    print(xs[0][0].v)


def main() -> None:
    h([(A(1), 10)])


main()
