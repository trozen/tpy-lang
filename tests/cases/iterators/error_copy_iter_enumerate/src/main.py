# `enumerate` lends its steps (`Iterable[T]`), so it refuses `copy_iter` of
# reference elements, which hands them over; the remedy collects them first.
from tpy import int32, copy_iter


class Cell:
    def __init__(self, v: int32) -> None:
        self.v = v


def main() -> None:
    cells = [Cell(1), Cell(2)]
    # the refused enumerate and its remedy
    for i, c in enumerate(copy_iter(cells)):  # tpyc: error(/No matching overload for 'enumerate'.*'Iterator\[Own\[Cell\]\]' hands over its steps; collect them first \(list\(\.\.\.\)\)/)
        print(i, c.v)


main()
