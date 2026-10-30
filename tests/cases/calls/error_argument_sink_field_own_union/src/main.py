# A union field at an Own[A | B] parameter stays refused until an owned
# union parameter narrows mutably (BUGS.md#own-union-param-isinstance-extract-const).
from tpy import Own, int32


class A:
    def __init__(self, n: int32) -> None:
        self.n = n


class B:
    def __init__(self, n: int32) -> None:
        self.n = n


class H:
    def __init__(self, u: A | B) -> None:
        self.u = u


def eat(u: Own[A | B]) -> int32:
    return 1


def main() -> None:
    h = H(A(1))
    print(eat(h.u))  # tpyc: error(/call\.arg_shape\.own_union/)


main()
