# The open-`T` tuple element admits a plain declared NAME source; a subscript
# read under `copy()` renders differently and keeps rejecting.
from tpy import int32, Own, copy


class Point:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


class Bag[T]:
    items: list[T]

    def __init__(self) -> None:
        self.items = []

    def add(self, v: Own[T]) -> None:
        self.items.append(v)

    def pairs(self) -> Own[list[tuple[T, int32]]]:
        out: list[tuple[T, int32]] = []
        out.append((copy(self.items[0]), 1))  # tpyc: error(/expr.container_literal/)
        return out


def main() -> None:
    b: Bag[Point] = Bag()
    b.add(Point(1))
    print(len(b.pairs()))


main()
