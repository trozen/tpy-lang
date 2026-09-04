# A field with only a class-level default is initialized by an NSDMI, which
# runs after the whole member-init list -- so a member init cannot read it.
# The init demotes to the constructor body (where the default IS in place),
# and there the container comprehension has no lowering row of its own.
from tpy import Int32


class Grid:
    n: Int32 = Int32(4)
    cells: list[Int32]

    def __init__(self) -> None:
        # The subject: the comprehension reads a default-only field.
        self.cells = [i for i in range(self.n)]  # tpyc: error(/expr\.list_comp/)


def main() -> None:
    g = Grid()
    print(g.n, len(g.cells))


main()
