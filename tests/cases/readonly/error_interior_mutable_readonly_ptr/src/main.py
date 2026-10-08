# `unsafe_interior_mutable[readonly[Ptr[T]]]` gets the same migration error as
# the plain Ptr form: readonly does not reach through a Ptr, so the marker is
# not needed.
from tpy import Ptr, int32, readonly, unsafe_interior_mutable


class Cell:
    count: int32

    def __init__(self) -> None:
        self.count = 0


class Handle:
    _cell: unsafe_interior_mutable[readonly[Ptr[Cell]]]  # tpyc: error(/no longer needed: readonly does not reach through a Ptr, so declare the field as 'readonly\[Ptr\[Cell\]\]'/)

    def __init__(self, c: Cell) -> None:
        self._cell = c


def main() -> None:
    pass


main()
