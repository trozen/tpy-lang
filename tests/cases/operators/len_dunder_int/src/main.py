# A user-class __len__ may be declared `-> int` (the natural Python spelling),
# not only `-> Int32`; len() converts the BigInt result to size_t.
from tpy import Int32


class Bag:
    _items: list[Int32]

    def __init__(self) -> None:
        self._items = [10, 20, 30]

    def add(self, x: Int32) -> None:
        self._items.append(x)

    def __len__(self) -> int:
        return len(self._items)


def main() -> None:
    b = Bag()
    print(len(b))                 # 3
    b.add(40)
    print(len(b))                 # 4 -- __len__ reflects mutated state
    print(len(b) == 4)            # len() in a comparison context


main()
