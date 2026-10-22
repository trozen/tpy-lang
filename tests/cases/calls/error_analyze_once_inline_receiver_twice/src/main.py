# An @inline method whose body uses `self` twice, called on a consuming
# call: each use is the receiver evaluated again, so the second use reads a
# consumed local (the receiver is not yet evaluated once:
# BUGS.md#inline-argument-evaluated-per-use).
from typing import Self
from tpy import Own, int32, inline


class B:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v

    def take(self: Own[Self]) -> Own[Self]:
        return self

    @inline
    def twice(self) -> None:
        show2(self, self)


def show2(a: B, b: B) -> None:
    print(a.v, b.v)


def main() -> None:
    b = B(1)
    b.take().twice()  # tpyc: error(/Cannot use 'b' after it was consumed/)


main()
