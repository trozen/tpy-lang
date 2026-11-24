# A chained comparison's fresh end operand at a dunder parameter typed as a
# union of records is refused (BUGS.md#ptr-union-fresh-operator-operand-refused).
from tpy import Own, int32


class Base:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __lt__(self, other: "Base | Mid") -> bool:
        if isinstance(other, Mid):
            return self.n < other.n
        return self.n < other.n


class Mid(Base):
    def __init__(self, n: int32) -> None:
        super().__init__(n)


class Leaf(Mid):
    def __init__(self, n: int32) -> None:
        super().__init__(n)


def make_leaf() -> Own[Leaf]:
    return Leaf(9)


def run() -> None:
    b = Base(5)
    print(b < make_leaf() < Leaf(10))  # tpyc: error(/operator.operand.materialize/)


run()
