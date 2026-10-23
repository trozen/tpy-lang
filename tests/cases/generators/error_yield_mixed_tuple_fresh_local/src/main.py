# Yielding a fresh-record tuple local at `tuple[Own[A], B]`: B has no storage.
# docs/LANGUAGE_FEATURES.md "Borrow Form vs Storage Form".
from typing import Iterator
from tpy import Own, int32


class A:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class B:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


def gen() -> Iterator[tuple[Own[A], B]]:
    t = (A(1), B(2))
    # the fresh B is the subject
    yield t  # tpyc: error(/would dangle/)


def main() -> None:
    for q in gen():
        print(q[0].v, q[1].v)


main()
