# A violated class-shadowed method bound through a GENERIC subclass receiver
# (the composed-subst path: the shadowed param binds through the child's own
# type param) must get the clean bound diagnostic. Companion to
# error_method_bound_shadow_subclass, which pins the plain-subclass shape.
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


class WideBag[U](Bag[U, 8]):
    pass


def main() -> None:
    b = WideBag[NotEquatable]()
    n = NotEquatable(1)
    if b.contains(n):  # tpyc: error(/requires type parameter 'T' to satisfy 'Equatable'/)
        print("found")


main()
