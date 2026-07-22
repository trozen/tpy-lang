# A nested def in __init__ capturing and mutating self: constructors are
# a distinct emission context from ordinary methods.
from tpy import Int32


class Grid:
    w: Int32
    cells: Int32

    def __init__(self, w: Int32) -> None:
        self.w = w
        self.cells = 0

        def expand() -> None:
            self.cells += self.w

        expand()
        expand()


def main() -> None:
    g = Grid(4)
    print(g.cells)


main()
