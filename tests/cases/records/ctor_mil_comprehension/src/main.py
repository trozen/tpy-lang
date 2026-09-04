# A comprehension initializing a container field from the constructor: its
# statement-expression goes straight into the member-init list, and it may read
# only a field the member-init list has ALREADY initialized -- struct members
# are ordered by `__init__` assignment order, so `n` (assigned first) holds its
# value here while a later-assigned or default-only field would not.
from tpy import Int32


class Cell:
    v: Int32

    def __init__(self, v: Int32) -> None:
        self.v = v


class Grid:
    n: Int32
    cells: list[Cell]
    seen: set[Int32]
    index: dict[Int32, Int32]

    def __init__(self, n: Int32) -> None:
        self.n = n
        self.cells = [Cell(i) for i in range(self.n)]  # tpyc: ok
        self.seen = {i * 2 for i in range(self.n)}  # tpyc: ok
        self.index = {i: i * i for i in range(self.n)}  # tpyc: ok


def main() -> None:
    g = Grid(3)
    # Mutating through the field proves the member init built it in place.
    g.cells[0].v = 9
    print(len(g.cells), g.cells[0].v, g.cells[2].v)
    print(len(g.seen), 4 in g.seen)
    print(len(g.index), g.index[2])


main()
