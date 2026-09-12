# 3-member inline union `isinstance(v, A | B | C)` exercises the operand
# flatten at depth 2 (`(A | B) | C`); the negative branch narrows v to the
# excluded member D and mutates it through the narrowed binding.
from tpy import int32


class A:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class B:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class C:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class D:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def classify(v: A | B | C | D) -> int32:
    if not isinstance(v, A | B | C):
        v.n += 100
        return v.n
    return 0


def main() -> None:
    print(classify(A(1)))
    print(classify(D(5)))


main()
