# An augmented subscript through a call receiver whose `__setitem__` takes
# the receiver by ownership is refused: the receiver is evaluated once and
# read by both dunders (BUGS.md#aug-consuming-subscript-dunder).
from typing import Self
from tpy import Own, int32


class Box:
    def __getitem__(self, k: int32) -> int32:
        return 2

    def __setitem__(self: Own[Self], k: int32, value: int32) -> None:
        print("set", value)


def make() -> Own[Box]:
    return Box()


def main() -> None:
    # The held receiver cannot be both read in place and consumed
    make()[0] += 1  # tpyc: error(/the receiver here is evaluated once and read by both `__getitem__` and `__setitem__`, but `__setitem__` takes it by ownership/)


main()
