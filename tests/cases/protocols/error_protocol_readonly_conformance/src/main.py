# Record with @readonly(False) on __getitem__ should fail Sequence conformance
# because Sequence is a readonly protocol.
from tpy import Int32, readonly
from typing import Sequence


class BadContainer:
    items: list[Int32]

    def __init__(self, items: list[Int32]) -> None:
        self.items = items

    def __len__(self) -> Int32:
        return len(self.items)

    @readonly(False)
    def __getitem__(self, index: Int32) -> Int32:
        return self.items[index]


def read_first(s: Sequence[Int32]) -> Int32:
    return s[0]


c = BadContainer([1, 2, 3])
print(read_first(c))  # tpyc: error(/does not conform/)
