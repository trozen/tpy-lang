# A record on the left whose comparison dunder refuses the operand is not
# passed over for the right operand's reflected one: CPython would call
# `A.__eq__` first, which TPy cannot, so `A(1) == B(1)` rejects rather than
# silently running `B.__eq__` (BUGS.md#comparison-operand-unanswered-reject).
from tpy import int32


class A:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v

    def __eq__(self, o: "A") -> bool:
        return self.v == o.v


class B:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v

    def __eq__(self, o: A) -> bool:
        return False


def main() -> None:
    a = A(1)
    b = B(1)
    print(a == b)  # tpyc: error(/binop\.shape\.==\.record/)


main()
