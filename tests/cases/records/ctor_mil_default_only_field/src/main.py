# A field with only a class-level default is initialized by an NSDMI, which
# runs after the whole member-init list -- so a member init cannot read it.
# The init demotes to the constructor body (where the default IS in place),
# and there the container comprehension takes the field-write render
# (`this->cells = ({ ... });`); the list is grown afterwards through the
# field so a silent copy would show.
from tpy import Int32


class Grid:
    n: Int32 = Int32(4)
    cells: list[Int32]

    def __init__(self) -> None:
        # The subject: the comprehension reads a default-only field.
        self.cells = [i for i in range(self.n)]  # tpyc: ok


def main() -> None:
    g = Grid()
    g.cells.append(9)
    print(g.n, len(g.cells), g.cells[4])


main()
