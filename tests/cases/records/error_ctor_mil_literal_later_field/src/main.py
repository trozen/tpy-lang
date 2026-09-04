# The container-LITERAL twin of the comprehension ordering reject: the same
# member-init rule applies to every source, so a literal element reading a
# field assigned later in `__init__` is rejected too.
from tpy import Int32


class Grid:
    cells: list[Int32]
    n: Int32

    def __init__(self, n: Int32) -> None:
        # The subject: a literal element reads a field initialized after it.
        self.cells = [self.n, Int32(1)]  # tpyc: error(/ctor\.mil_reads_unready_field/)
        self.n = n


def main() -> None:
    g = Grid(3)
    print(g.cells[0], g.n)


main()
