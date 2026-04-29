# Multi-base inheritance: when every declaring ancestor's ClassVar has
# matching finality and type, the child's shadow is accepted. Each class
# (A, B, C) gets its own `static inline` slot.
from typing import ClassVar
from tpy import Int32


class A:
    X: ClassVar[Int32] = 1


class B:
    X: ClassVar[Int32] = 2


class C(A, B):
    X: ClassVar[Int32] = 3


def main() -> None:
    A.X = 10
    B.X = 20
    C.X = 30
    print(A.X)
    print(B.X)
    print(C.X)


main()
