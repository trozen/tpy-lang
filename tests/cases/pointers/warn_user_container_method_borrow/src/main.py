# Non-readonly methods on user-defined types should warn when the object
# has active iter borrows and the method structurally mutates self.
# Methods that only mutate unrelated fields (set_label) should NOT warn.
from tpy import Int32
from typing import Iterator

class NumberList:
    _items: list[Int32]
    _label: str

    def __init__(self, label: str) -> None:
        self._items = []
        self._label = label

    def add(self, val: Int32) -> None:
        self._items.append(val)

    def set_label(self, label: str) -> None:
        self._label = label

    def __iter__(self) -> Iterator[Int32]:
        return iter(self._items)

def main() -> None:
    nl = NumberList("test")
    nl.add(1)
    nl.add(2)
    for x in nl:
        nl.add(3)  # tpyc: warning(/Mutation of 'nl' while iterating/)
        nl.set_label("updated")  # tpyc: ok
        print(x)
        break

main()
