from tpy import int32


class Cell:
    v: int32

    def __init__(self, v: int32) -> None:
        self.v = v


V = Cell(2)
# A tuple of references: the tuple of pointer slots, aliasing V.
pair: tuple[Cell, int32] = (V, 1)
# A fresh element: the global owns it (storage).
owned: tuple[int32, Cell] = (1, Cell(5))
