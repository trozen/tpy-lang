# Mutual recursion + __copy__ on both records + a large __del__ on A: the copy
# ops define after both structs, and A's big dtor/move family lands in the .cpp.
from __future__ import annotations
from tpy import Int32, Own, copy


class A:
    val: Int32
    bs: list[B]

    def __init__(self, val: Int32) -> None:
        self.val = val
        self.bs = []

    def __copy__(self) -> Own[A]:
        return A(self.val + 100)

    def __del__(self) -> None:
        print("A del begin", self.val)
        s = self.val
        s += 1
        s += 2
        s += 3
        s += 4
        s += 5
        print("A del end", s)


class B:
    val: Int32
    as_: list[A]

    def __init__(self, val: Int32) -> None:
        self.val = val
        self.as_ = []

    def __copy__(self) -> Own[B]:
        return B(self.val + 100)


def copied_val(a: A) -> Int32:
    a2 = copy(a)
    return a2.val


def main() -> None:
    a = A(1)
    a.bs.append(B(2))
    c = copied_val(a)
    a.bs[0].val = 20
    print(a.val, a.bs[0].val, c, len(a.bs))


main()
