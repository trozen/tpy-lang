# `m[0] += 2` through `Item.__add__ -> int32`: sema checks the result against
# `__setitem__`'s value parameter, which takes a `str`, and refuses it.
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

    def __setitem__(self, key: int32, value: str) -> None:
        self.item = Item(len(value))


def main() -> None:
    m = Mapping()
    m[0] += 2  # tpyc: error(/Type mismatch in argument 'value': expected str, got int32/)
    print(m[0].n)


main()
