# A readonly method's borrow-form tuple RETURN is rendered with const element
# pointers (`std::tuple<const Cell*, ...>`), so the returned tuple literal has
# to be built that way too -- the body used to build mutable pointers against
# its own const signature and fail the C++ build. Covers the inferred-readonly
# method, the explicitly decorated one, and the mixed owned+borrow return.
#
# `Cell` is @nocopy so a regression to whole-tuple storage form is a compile
# error rather than a silent copy: the borrowed element cannot be copied in.
from tpy import Int32, Own, nocopy, readonly


@nocopy
class Cell:
    val: Int32

    def __init__(self, val: Int32) -> None:
        self.val = val


class Maker:
    n: Int32

    def __init__(self) -> None:
        self.n = 0

    # Never mutates self -- readonly is INFERRED, and with it the const return.
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
