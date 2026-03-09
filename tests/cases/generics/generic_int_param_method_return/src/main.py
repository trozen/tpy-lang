# Int type param substitution in method return types
from __future__ import annotations
from tpy import Int32, Own


class Grid[T, N: int]:
    _value: T

    def __init__(self, value: T) -> None:
        self._value = value

    def copy(self) -> Own[Grid[T, N]]:
        return Grid[T, N](self._value)

    def with_value(self, value: T) -> Own[Grid[T, N]]:
        return Grid[T, N](value)


def main() -> None:
    g = Grid[Int32, 4](10)
    g2 = g.copy()
    g3 = g.with_value(99)
    print(g._value)
    print(g2._value)
    print(g3._value)


main()
