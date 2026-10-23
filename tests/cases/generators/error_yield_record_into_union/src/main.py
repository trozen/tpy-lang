# A record parameter yielded at a union slot: the step holds the union's
# storage, so the yield would copy the caller's record -- a copy sema did not
# declare (CPython hands the object itself on). It is refused
# (BUGS.md#yield-record-into-union-slot-refused).
from typing import Iterator
from tpy import int32


class A:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class B:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


# the reject is located at the frame's def, not at the yield inside it
def gen(a: A) -> Iterator[A | B]:  # tpyc: error(/return\.copy_undeclared/)
    yield a


def main() -> None:
    a = A(1)
    for b in gen(a):
        if isinstance(b, A):
            b.v = 99
    print(a.v)


main()
