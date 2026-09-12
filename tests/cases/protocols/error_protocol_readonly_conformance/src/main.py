# Record with @readonly(False) on __getitem__ should fail Sequence conformance
# because Sequence is a readonly protocol.
from tpy import int32, readonly
from typing import Sequence


class BadContainer:
    items: list[int32]

    def __init__(self, items: list[int32]) -> None:
        self.items = items

    def __len__(self) -> int32:
        return len(self.items)

    @readonly(False)
    def __getitem__(self, index: int32) -> int32:
        return self.items[index]


def read_first(s: Sequence[int32]) -> int32:
    return s[0]


c = BadContainer([1, 2, 3])
print(read_first(c))  # tpyc: error(/does not conform/)
