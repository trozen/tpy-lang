# Arity-variant @overload methods with a single impl using defaults
from typing import overload


class Calculator:
    offset: int

    def __init__(self, offset: int) -> None:
        self.offset = offset

    @overload
    def scale(self, x: int) -> int: ...  # tpyc: ok

    @overload
    def scale(self, x: int, factor: int) -> int: ...  # tpyc: ok

    def scale(self, x: int, factor: int = 1) -> int:
        return (x * factor) + self.offset


def main() -> None:
    c = Calculator(10)
    print(c.scale(5))
    print(c.scale(5, 3))


main()
