# Regression: method-level type param shadows class T with a tighter bound
# (`def __eq__[T: Equatable](...)` on `class Box[T]`). The class-level T must
# satisfy the method-level bound at every dispatch site, including operators.
from tpy import Equatable

class NotEquatable:
    x: int

    def __init__(self, x: int) -> None:
        self.x = x


class Box[T]:
    value: T

    def __init__(self, value: T) -> None:
        self.value = value

    def __eq__[T: Equatable](self, other: Box[T]) -> bool:
        return self.value == other.value


def main() -> None:
    a = Box(NotEquatable(1))
    b = Box(NotEquatable(2))
    if a == b:  # tpyc: error(/requires type parameter 'T' to satisfy 'Equatable'/)
        print("equal")


main()
