# A value-bound generic (`T: ValueType`) whose ctor and a method write an
# `Own[T]` param into a `T` field: the member-init moves it, the method copies.
from tpy import int32, Own, ValueType


class Cell[T: ValueType]:
    item: T

    def __init__(self, item: Own[T]) -> None:
        self.item = item  # tpyc: ok

    def replace(self, item: Own[T]) -> int32:
        self.item = item  # tpyc: ok
        return 1


def main() -> None:
    c: Cell[int32] = Cell[int32](11)
    print(c.item)
    c.replace(22)
    print(c.item)


main()
