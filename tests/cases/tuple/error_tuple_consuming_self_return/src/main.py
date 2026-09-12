# A consuming method's self dies at method exit: returning a borrow-form
# tuple read from its field would dangle, same as an Own param root.
from typing import Self
from tpy import int32, Own


class Box:
    val: int32
    def __init__(self, v: int32) -> None:
        self.val = v


class Wrap:
    pair: tuple[int32, Box]
    def __init__(self, b: Box) -> None:
        self.pair = (1, b)
    def into_pair(self: Own[Self]) -> tuple[int32, Box]:
        return self.pair  # tpyc: error(/storage owned by the function/)


def main() -> None:
    pass


main()
