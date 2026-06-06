# An Own[A | B] param is callee-owned and dies at function exit: returning
# it into a borrow-form pointer-variant Union return must be rejected.
from tpy import Int32, Own


class A:
    x: Int32
    def __init__(self, x: Int32) -> None:
        self.x = x


class B:
    y: Int32
    def __init__(self, y: Int32) -> None:
        self.y = y


def ret_union(u: Own[A | B]) -> A | B:
    return u  # tpyc: error(/Cannot return local or temporary/)


def main() -> None:
    pass


main()
