# Regression: after `if self.field is None: return`, reassigning the
# same field to None must still emit `std::nullopt` against the
# `std::optional<Box<T>>` storage, not bare `nullptr`.
from tplib import Box


class Cell:
    v: int
    def __init__(self, v: int) -> None:
        self.v = v


class Holder:
    slot: Box[Cell] | None

    def __init__(self) -> None:
        self.slot = Box(Cell(1))

    def finish(self) -> None:
        if self.slot is None:
            return
        print(self.slot.get().v)
        self.slot = None


def main() -> None:
    h = Holder()
    h.finish()


main()
