# The callees the main module's cross-module sections lend through.
from tpy import int32


class Cell:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def bump(self) -> None:
        self.n += 1


class Chooser:
    k: int32

    def __init__(self) -> None:
        self.k = 0

    # Writes nothing of its own receiver: inferred readonly.
    def pick(self, x: Cell) -> Cell:
        return x


class Crate:
    cell: Cell

    def __init__(self, n: int32) -> None:
        self.cell = Cell(n)


def opt_cell(c: Crate) -> Cell | None:
    return c.cell
