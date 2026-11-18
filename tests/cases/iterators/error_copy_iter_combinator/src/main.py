# A builtin combinator's lending `Iterable[T]` parameters refuse sources that
# hand their steps over (`copy_iter` of reference elements), one remedy per
# refused argument at the no-match error (docs/LANGUAGE_FEATURES.md "Copy
# Warnings for Inline Storage").
from tpy import int32, copy_iter


class Cell:
    def __init__(self, v: int32) -> None:
        self.v = v


def main() -> None:
    cells = [Cell(1), Cell(2)]
    # annotated: BUGS.md#generic-builtin-nested-literal-local-no-overload
    rows: list[list[int32]] = [[1], [2]]
    # the refused zip, both arguments with their remedy
    for c, r in zip(copy_iter(cells), copy_iter(rows)):  # tpyc: error(/No matching overload for 'zip'.*'Iterator\[Own\[Cell\]\]' hands over its steps; collect them first \(list\(\.\.\.\)\); 'Iterator\[Own\[list\[int32\]\]\]' hands over its steps; collect them first/)
        print(c.v, len(r))


main()
