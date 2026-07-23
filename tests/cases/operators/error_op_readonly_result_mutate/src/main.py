# Mutation through the result of a readonly[...]-returning operator dunder is
# rejected at sema: the binding is a readonly borrow of the operand.
from tpy import Int32, readonly


class Acc:
    n: Int32

    def __init__(self, n: Int32):
        self.n = n

    def __add__(self, o: "Acc") -> "readonly[Acc]":
        return self


def main():
    a = Acc(3)
    b = Acc(1)
    c = a + b
    c.n = 5  # tpyc: error(/readonly/)
    print(c.n)


main()
