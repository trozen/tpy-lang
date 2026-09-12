# An Own[A | B] param is callee-owned and dies at function exit: returning
# it into a borrow-form pointer-variant Union return must be rejected.
from tpy import int32, Own


class A:
    x: int32
    def __init__(self, x: int32) -> None:
        self.x = x


class B:
    y: int32
    def __init__(self, y: int32) -> None:
        self.y = y


def ret_union(u: Own[A | B]) -> A | B:
    return u  # tpyc: error(/Cannot return local or temporary/)


def main() -> None:
    pass


main()
