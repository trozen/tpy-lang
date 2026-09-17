# The readonly[...] spelling of a borrow-returning operator dunder: the
# result binds as a readonly borrow (sema type readonly[Acc], C++ const Acc&)
# that still aliases the operand -- source mutation is visible through it,
# matching CPython. Mutation THROUGH the result is sema-rejected (see
# error_op_readonly_result_mutate).
from tpy import int32, readonly


class Acc:
    n: int32

    def __init__(self, n: int32):
        self.n = n

    def __add__(self, o: "Acc") -> "readonly[Acc]":
        return self if self.n >= o.n else o

    def __neg__(self) -> "readonly[Acc]":
        return self


def test_binary():
    a = Acc(3)
    b = Acc(1)
    c = a + b
    print(c.n)
    a.n = 21  # tpyc: ok
    print(c.n)


def test_unary():
    a = Acc(2)
    c = -a
    a.n = 5  # tpyc: ok
    print(c.n)


def main():
    test_binary()
    test_unary()


main()
