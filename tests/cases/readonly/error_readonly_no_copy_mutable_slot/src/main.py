# Guard on the copy() admission: copy() makes its RESULT owned and mutable, it
# does not relax readonly[T] -> T for the payload itself.
from tpy import int32, readonly


class Cell:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def mutate(c: Cell) -> None:
    c.n = c.n + 1


def sec_free(c: readonly[Cell]) -> None:
    # SUBJECT: no copy() spelled, so the borrow cannot fill a mutable slot.
    mutate(c)  # tpyc: error(/Cannot pass readonly\[Cell\] as mutable Cell/)
