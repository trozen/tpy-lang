# `t[x] += 1` passes the key to `__getitem__` and to `__setitem__`: one
# getter width leaves it open there, but the setters differ in width at the
# key, so the use fixes the pending local and a later wider store is refused.
from tpy import dispatch, int32, int64


def wide() -> int64:
    return 7


class Grid:
    cells: list[int32]

    def __init__(self) -> None:
        self.cells = [0, 0, 0]

    def __getitem__(self, i: int32) -> int32:
        return self.cells[i]

    @dispatch
    def __setitem__(self, i: int32, v: int32) -> None:
        self.cells[i] = v

    @dispatch
    def __setitem__(self, i: int64, v: int32) -> None:
        self.cells[i] = v


def main() -> None:
    g = Grid()
    xs = [1, 2]
    for x in xs:
        g[x] += 1
    # The augmented store above fixed the elements at int32.
    xs.append(wide())  # tpyc: error(/'xs' holds int32 elements since line 33 \(through 'x', a subscript key\), and this value is int64/)


main()
