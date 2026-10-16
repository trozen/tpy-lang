# A non-owning local (LANGUAGE_FEATURES.md "Owning and non-owning locals") copies at
# an `Own` return even after `b = Cell(2)`; a @nocopy type cannot, located error.
from tpy import Own, int32, nocopy


@nocopy
class Cell:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def overwrite(w: Cell) -> Own[Cell]:
    b = w
    print(b.n)
    b = Cell(2)
    return b  # tpyc: error(/cannot be returned as return type Own\[Cell\]\. Only the original owner can be moved at its last use \('b' is bound to existing storage at line 15\)/)


print(overwrite(Cell(1)).n)
