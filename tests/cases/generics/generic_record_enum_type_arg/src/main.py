# An enum as a generic record's type argument, spelled BOTH ways -- explicit
# and inferred from the member -- and read back through the same accessor.
from enum import Enum


class Color(Enum):
    RED = 1
    BLUE = 2


class Box[T]:
    value: T

    def __init__(self, value: T) -> None:
        self.value = value


def main() -> None:
    b = Box[Color](Color.RED)  # tpyc: ok -- the explicit type argument
    print(b.value == Color.RED, b.value == Color.BLUE)
    inferred = Box(Color.BLUE)  # tpyc: ok -- T inferred from the enum member
    print(inferred.value == Color.RED, inferred.value == Color.BLUE)


main()
