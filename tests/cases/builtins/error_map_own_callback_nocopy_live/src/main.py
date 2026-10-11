# A multi-input map whose callback takes `Own[Cell]`, fed a live container of
# @nocopy elements: the iterable is an owning element slot, and copying a
# @nocopy element into it is an error (a dead source would move instead).
from tpy import int32, Own, nocopy


@nocopy
class Cell:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def take(k: int32, c: Own[Cell]) -> int32:
    return c.n + k


def main() -> None:
    cells: list[Cell] = [Cell(7)]
    for v in map(take, [1], cells):  # tpyc: error(/cannot copy non-copyable type 'Cell' into owned storage/)
        print(v)
    print(cells[0].n)


main()
