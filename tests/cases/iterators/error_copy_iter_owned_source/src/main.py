# `copy_iter` of another `copy_iter` is refused at its lending `Iterable[T]`
# parameter: the inner one already hands its steps over
# (docs/LANGUAGE_FEATURES.md "Copy Warnings for Inline Storage").
from tpy import int32, copy_iter


class Cell:
    def __init__(self, v: int32) -> None:
        self.v = v


def main() -> None:
    cells = [Cell(1), Cell(2)]
    # the refused double copy
    print(len(list(copy_iter(copy_iter(cells)))))  # tpyc: error(/'Iterator\[Own\[Cell\]\]' hands over its steps; collect them first/)


main()
