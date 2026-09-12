# The container-LITERAL twin of the comprehension ordering reject: the same
# member-init rule applies to every source, so a literal element reading a
# field assigned later in `__init__` is rejected too.
from tpy import int32


class Grid:
    cells: list[int32]
    n: int32

    def __init__(self, n: int32) -> None:
        # The subject: a literal element reads a field initialized after it.
        self.cells = [self.n, int32(1)]  # tpyc: error(/ctor\.mil_reads_unready_field/)
        self.n = n


def main() -> None:
    g = Grid(3)
    print(g.cells[0], g.n)


main()
