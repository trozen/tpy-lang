# A consuming call takes a borrowed @nocopy parameter as an owning sink (LANGUAGE_FEATURES.md
# "Owning and non-owning locals"): there is no copy to make, so it is the located error.
from typing import Self
from tpy import Own, int32, nocopy


@nocopy
class Cell:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def finish(self: Own[Self]) -> Own[Self]:
        return self


def f(w: Cell) -> Own[Cell]:
    return w.finish()  # tpyc: error(/@nocopy type 'Cell' cannot be passed as the consuming receiver Own\[Cell\]/)


print(f(Cell(1)).n)
