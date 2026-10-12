# `is` with a union-of-classes operand is not supported yet
# (BUGS.md#identity-ref-union-rejects).
class A:
    n: int

    def __init__(self) -> None:
        self.n = 1


class B:
    n: int

    def __init__(self) -> None:
        self.n = 2


def same(x: A | B, a: A) -> bool:
    return x is a  # tpyc: error(/a union operand \('A \| B'\) is not supported yet/)


a = A()
print(same(a, a))
