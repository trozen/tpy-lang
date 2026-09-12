# A member init may read only a self field the member-init list has already
# initialized. Struct members are ordered by `__init__` assignment order, so a
# field assigned LATER holds nothing yet -- CPython raises AttributeError here.
from tpy import int32


class Grid:
    cells: list[int32]
    n: int32

    def __init__(self, n: int32) -> None:
        # The subject: the comprehension reads a field initialized after it.
        self.cells = [i for i in range(self.n)]  # tpyc: error(/ctor\.mil_reads_unready_field/)
        self.n = n


def main() -> None:
    g = Grid(3)
    print(len(g.cells), g.n)


main()
