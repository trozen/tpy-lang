# A leading body statement breaks the member-init chain, so every init after
# it runs in the constructor BODY in source order -- and there a read of a
# field an earlier body init already wrote is in place. The ordering rule is
# about where an init actually runs, not about where it was written.
from tpy import Int32


class Cell:
    v: Int32

    def __init__(self, v: Int32) -> None:
        self.v = v


class Grid:
    n: Int32
    cells: list[Int32]
    boxes: list[Cell]

    def __init__(self, xs: list[Int32]) -> None:
        m = len(xs) + 1  # breaks the chain: everything below runs in the body
        self.n = m
        # The subject: both read `self.n`, written by the body init above.
        self.cells = [self.n, Int32(1)]  # tpyc: ok
        self.boxes = [Cell(self.n)]  # tpyc: ok


def main() -> None:
    g = Grid([Int32(7)])
    print(g.n, g.cells[0], g.cells[1])
    # Mutating through the field proves the element is stored in place.
    g.boxes[0].v = 9
    print(g.boxes[0].v)


main()
