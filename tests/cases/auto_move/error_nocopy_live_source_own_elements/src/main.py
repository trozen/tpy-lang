# A live container of @nocopy elements at an `Iterable[Own[T]]` parameter: each
# element would have to be copied into the loop variable, which the type
# forbids, so the call is an error (a dead source would move instead).
from tpy import int32, Own, nocopy
from typing import Iterable


@nocopy
class Cell:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def inspect(source: Iterable[Own[Cell]]) -> int32:
    total: int32 = 0
    for c in source:
        total += c.n
    return total


def main() -> None:
    cells: list[Cell] = [Cell(7)]
    print(inspect(cells))  # tpyc: error(/cannot copy non-copyable type 'Cell' into owned storage/)
    print(cells[0].n)


main()
