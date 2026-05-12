# Regression: augmented assignment dispatch (`+=`) on a generic container
# whose __iadd__ has `[T: Equatable]` shadowing class T. The class-shadowed
# bound must be enforced at the resolve_aug_inplace dispatch site.
from tpy import Equatable


class NotEquatable:
    x: int

    def __init__(self, x: int) -> None:
        self.x = x


class Accumulator[T]:
    items: list[T]

    def __init__(self) -> None:
        self.items = []

    def __iadd__[T: Equatable](self, other: T) -> Accumulator[T]:
        for it in self.items:
            if it == other:
                return self
        self.items.append(other)
        return self


def main() -> None:
    a: Accumulator[NotEquatable] = Accumulator()
    n = NotEquatable(1)
    a += n  # tpyc: error(/requires type parameter 'T' to satisfy 'Equatable'/)


main()
