# Returning a borrow-form tuple read from local container storage: the
# lift would take element addresses into a list that dies with the function.
from tpy import Int32


class Box:
    val: Int32
    def __init__(self, v: Int32) -> None:
        self.val = v


def ret() -> tuple[Int32, Box]:
    items: list[tuple[Int32, Box]] = [(1, Box(5))]
    return items[0]  # tpyc: error(/storage owned by the function/)


def main() -> None:
    pass


main()
