# `m[0] += 2` through `Item.__add__ -> int32` into `__setitem__(value: int32)`: sema accepts it,
# the lowering refuses the store (BUGS.md#aug-subscript-dunder-fallback-rejects).
from tpy import ValueType, int32


class Item(ValueType):
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __add__(self, rhs: int32) -> int32:
        return self.n + rhs


class Mapping:
    item: Item

    def __init__(self) -> None:
        self.item = Item(5)

    def __getitem__(self, key: int32) -> Item:
        return self.item

    def __setitem__(self, key: int32, value: int32) -> None:
        self.item = Item(value)


def main() -> None:
    m = Mapping()
    m[0] += 2  # tpyc: error(/not yet supported by C\+\+ code generation \(stmt.aug_assign\)/)
    print(m[0].n)


main()
