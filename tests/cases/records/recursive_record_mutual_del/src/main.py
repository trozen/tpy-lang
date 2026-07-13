# Mutually-recursive records where both carry __del__: the dtor/move ops must
# define after both structs (the ctor sibling is recursive_record_mutual).
from __future__ import annotations
from tpy import Int32


class A:
    val: Int32
    bs: list[B]

    def __init__(self, val: Int32) -> None:
        self.val = val
        self.bs = []

    def __del__(self) -> None:
        print("del A", self.val)


class B:
    val: Int32
    as_: list[A]

    def __init__(self, val: Int32) -> None:
        self.val = val
        self.as_ = []

    def __del__(self) -> None:
        print("del B", self.val)


def main() -> None:
    a = A(1)
    a.bs.append(B(2))
    a.bs[0].as_.append(A(3))
    a.bs[0].val = 20
    print(a.val, a.bs[0].val, len(a.bs[0].as_))


main()
