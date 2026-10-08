# `unsafe_interior_mutable` is no longer needed on a Ptr field: readonly does
# not reach through a Ptr.
from tpy import Ptr, int32, unsafe_interior_mutable


class Cell:
    count: int32

    def __init__(self) -> None:
        self.count = 0


class Handle:
    _cell: unsafe_interior_mutable[Ptr[Cell]]  # tpyc: error(/no longer needed: readonly does not reach through a Ptr/)

    def __init__(self, c: Cell) -> None:
        self._cell = c


def main() -> None:
    pass


main()
