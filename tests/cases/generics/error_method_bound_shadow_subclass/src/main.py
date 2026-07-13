# A violated class-shadowed method bound must produce the clean sema
# diagnostic through a SUBCLASS receiver too (regression: the subclass path
# misclassified the shadowed param and reported the unrelated "Cannot infer
# type arguments" instead of the bound violation).
from tpy import Equatable


class NotEquatable:
    x: int

    def __init__(self, x: int) -> None:
        self.x = x


class Bag[T, N: int]:
    items: list[T]

    def __init__(self) -> None:
        self.items = []

    def add(self, value: T) -> None:
        self.items.append(value)

    def contains[T: Equatable](self, value: T) -> bool:
        for it in self.items:
            if it == value:
                return True
        return False


class NEBag(Bag[NotEquatable, 4]):
    pass


def main() -> None:
    b = NEBag()
    n = NotEquatable(1)
    if b.contains(n):  # tpyc: error(/requires type parameter 'T' to satisfy 'Equatable'/)
        print("found")


main()
