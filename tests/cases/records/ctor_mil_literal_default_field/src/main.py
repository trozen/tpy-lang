# The happy face of the member-init ordering rule: a container literal reading
# a field that has only a class-level default demotes out of the member-init
# list into the constructor body, where the NSDMI has already run -- so the
# element sees the default (4) instead of an uninitialized slot.
from tpy import int32


class Cell:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


class Grid:
    n: int32 = int32(4)
    cells: list[int32]
    boxes: list[Cell]

    def __init__(self) -> None:
        # The subject: literal elements read a default-only field.
        self.cells = [self.n, int32(1)]  # tpyc: ok
        self.boxes = [Cell(self.n)]  # tpyc: ok


def main() -> None:
    g = Grid()
    print(g.n, g.cells[0], g.cells[1])
    # Mutating through the field proves the element is stored in place.
    g.boxes[0].v = 9
    print(g.boxes[0].v)


main()
