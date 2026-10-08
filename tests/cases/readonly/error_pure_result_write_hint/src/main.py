# A @pure method declares the reference it returns readonly: a write through
# its result is rejected, and the message names the way out (drop @pure).
from tpy import int32, pure


class Item:
    n: int32

    def __init__(self) -> None:
        self.n = 0


class Box:
    item: Item

    def __init__(self) -> None:
        self.item = Item()

    @pure
    def get(self) -> Item:
        return self.item


def main() -> None:
    b = Box()
    x = b.get()
    x.n = 5  # tpyc: error(/drop @pure/)


main()
