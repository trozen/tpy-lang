# A returned borrow-form tuple literal is built with the element slots its own
# signature spells. A DECLARED `@readonly` makes every parameter readonly, so
# its borrow return has const element pointers (`std::tuple<const Cell*, ...>`);
# an INFERRED one proves the receiver only, so a borrow of a PARAMETER keeps the
# declared mutable elements, as a free function's does. Also the mixed
# owned+borrow return.
#
# `Cell` is @nocopy so a regression to whole-tuple storage form is a compile
# error rather than a silent copy: the borrowed element cannot be copied in.
from tpy import int32, Own, nocopy, readonly


@nocopy
class Cell:
    val: int32

    def __init__(self, val: int32) -> None:
        self.val = val


class Maker:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    # Never mutates self, so the METHOD is const; the return borrows `c`, not
    # self, and stays mutable.
    def pair(self, c: Cell) -> tuple[Cell, Cell]:
        return (c, c)

    @readonly
    def declared_pair(self, c: Cell) -> tuple[Cell, Cell]:
        return (c, c)

    def mixed(self, c: Cell) -> tuple[Own[Cell], Cell]:
        return (Cell(1), c)

    # The mutating sibling keeps the non-const render -- the inverse guard.
    def bump_pair(self, c: Cell) -> tuple[Cell, Cell]:
        self.n = self.n + 1
        return (c, c)


def main() -> None:
    c = Cell(7)
    m = Maker()

    p = m.pair(c)
    print("pair:", p[0].val, p[1].val)

    d = m.declared_pair(c)
    print("declared:", d[1].val)

    x = m.mixed(c)
    print("mixed:", x[0].val, x[1].val)

    b = m.bump_pair(c)
    b[1].val = 42
    print("bump:", b[1].val, c.val, m.n)


main()
