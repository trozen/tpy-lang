# A frozen record's method declares the reference it returns readonly: a
# write through its result is rejected, and the message names the way out
# (mark the method @readonly(False)).
from dataclasses import dataclass
from tpy import int32


class Item:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    def __eq__(self, o: "Item") -> bool:
        return self.n == o.n

    def __hash__(self) -> int:
        return hash(self.n)


@dataclass(frozen=True)
class Frozen:
    item: Item

    def get(self) -> Item:
        return self.item


def main() -> None:
    f = Frozen(Item())
    f.get().n = 5  # tpyc: error(/mark it @readonly\(False\)/)


main()
