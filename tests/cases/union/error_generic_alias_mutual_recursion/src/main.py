# Mutual recursion involving a generic alias and a generic record (the alias
# Expr recurses through Op's Box[Expr[T]] field). v1 supports only direct
# identity recursion, so the transitive cycle is rejected with a clear
# diagnostic when the cycle detector tags Expr as recursive.
from tplib.box import Box


class Lit[T]:
    value: T

    def __init__(self, value: T) -> None:
        self.value = value


class Op[T]:
    child: Box[Expr[T]]

    def __init__(self, child: Box[Expr[T]]) -> None:
        self.child = child


# tpyc: error(/mutual recursion across generic aliases is not supported/)
type Expr[T] = Lit[T] | Op[T]


def main() -> None:
    pass


main()
