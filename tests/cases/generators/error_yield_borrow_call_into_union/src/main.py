# A borrowing call's union result yielded at a union slot: lifting the
# borrowed member into the step's storage copies the caller's record, a copy
# sema did not declare. It is refused
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


def borrow(a: A) -> A | B:
    return a


# the reject is located at the frame's def, not at the yield inside it
def gen(a: A) -> Iterator[A | B]:  # tpyc: error(/return\.copy_undeclared/)
    yield borrow(a)


def main() -> None:
    a = A(1)
    for b in gen(a):
        if isinstance(b, A):
            b.v = 99
    print(a.v)


main()
