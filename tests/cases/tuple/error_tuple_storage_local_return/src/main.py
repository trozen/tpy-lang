# Returning a borrow-form tuple read from local container storage: the
# lift would take element addresses into a list that dies with the function.
from tpy import int32


class Box:
    val: int32
    def __init__(self, v: int32) -> None:
        self.val = v


def ret() -> tuple[int32, Box]:
    items: list[tuple[int32, Box]] = [(1, Box(5))]
    return items[0]  # tpyc: error(/storage owned by the function/)


def main() -> None:
    pass


main()
