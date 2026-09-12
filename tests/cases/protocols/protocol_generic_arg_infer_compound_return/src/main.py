# Regression: a generic function `f[T](x: P[T])` should infer T from a
# concrete record arg whose protocol-conforming method has the record's
# class type param wrapped in a compound (here, `tuple[T, int32]`),
# both in return-type and parameter positions.
from typing import Protocol
from tpy import int32


class HasPair[T](Protocol):
    def pair(self) -> tuple[T, int32]: ...
    def consume(self, p: tuple[T, int32]) -> int32: ...


class Cell[T]:
    value: T

    def __init__(self, value: T) -> None:
        self.value = value

    def pair(self) -> tuple[T, int32]:
        return (self.value, int32(1))

    def consume(self, p: tuple[T, int32]) -> int32:
        return p[1]


# Return position: T inferred from `pair()`'s tuple[T, int32] return.
def second_of[T](s: HasPair[T]) -> int32:
    p = s.pair()
    return p[1]


# Parameter position: T inferred from `consume`'s tuple[T, int32] param.
def use_consume[T](s: HasPair[T], v: T) -> int32:
    return s.consume((v, int32(2)))


def main() -> None:
    c = Cell[int32](int32(42))
    print(second_of(c))                   # 1
    print(use_consume(c, int32(100)))     # 2


main()
