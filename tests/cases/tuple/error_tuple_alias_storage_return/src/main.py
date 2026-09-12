# Returning a tuple local that ALIASES local container storage (t = items[0])
# must be rejected like the direct subscript return: the borrow chain bottoms
# out in storage that dies with the function.
from tpy import int32


class Box:
    val: int32
    def __init__(self, v: int32) -> None:
        self.val = v


def ret() -> tuple[int32, Box]:
    items: list[tuple[int32, Box]] = [(1, Box(5))]
    t = items[0]
    return t  # tpyc: error(/storage owned by the function/)


def main() -> None:
    pass


main()
