# A fresh object at an operator dunder's parameter typed as a union of
# records is refused (BUGS.md#ptr-union-fresh-operator-operand-refused).
from tpy import int32


class Base:
    def __init__(self, n: int32) -> None:
        self.n = n


class Mid(Base):
    def __init__(self, n: int32) -> None:
        super().__init__(n)


class Leaf(Mid):
    def __init__(self, n: int32) -> None:
        super().__init__(n)


class Acc:
    def __init__(self) -> None:
        pass

    def __sub__(self, other: Base | Mid) -> int32:
        if isinstance(other, Mid):
            return other.n + 100
        return other.n


def run() -> None:
    a = Acc()
    print(a - Leaf(3))  # tpyc: error(/operator.operand.materialize/)


run()
