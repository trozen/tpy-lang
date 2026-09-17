# A borrow-returning operator dunder (binary, reflected, unary) hands out an
# ALIAS of an operand, like CPython: the friend-operator shim const-projects
# its return alongside the method emit, and the result binds as a borrow
# (mutating the source afterwards is visible through it), not an owned copy.
from tpy import int32


class Acc:
    n: int32

    def __init__(self, n: int32):
        self.n = n

    def __add__(self, o: "Acc") -> "Acc":
        return self if self.n >= o.n else o

    def __radd__(self, other: int32) -> "Acc":
        return self

    def __neg__(self) -> "Acc":
        return self


def test_binary_alias():
    a = Acc(3)
    b = Acc(1)
    c = a + b
    print(c.n)
    a.n = 99  # tpyc: ok
    print(c.n)


def test_binary_operand_alias():
    a = Acc(1)
    b = Acc(5)
    c = a + b
    b.n = 42  # tpyc: ok
    print(c.n)


def test_reflected_alias():
    a = Acc(4)
    c = 7 + a
    a.n = 11  # tpyc: ok
    print(c.n)


def test_unary_alias():
    a = Acc(6)
    c = -a
    a.n = 8  # tpyc: ok
    print(c.n)


def main():
    test_binary_alias()
    test_binary_operand_alias()
    test_reflected_alias()
    test_unary_alias()


main()
