# A pointer-repr `Optional[Array]` FIELD read binding a local: the
# `optional_to_ptr` lift a list/dict/set payload already took. Array is a
# reference type (value_form BORROW_REF), so the borrow ALIASES the field --
# the mutation between the binding and the read is what proves it.
from __future__ import annotations

from tpy import Array, int32


class Grid:
    cells: Array[int32, 2] | None

    def __init__(self) -> None:
        self.cells = [1, 2]

    def bump(self) -> None:
        a = self.cells
        if a is not None:
            a[0] = 9


def first_after_bump(g: Grid) -> int32:
    # The decl under test: `xs` binds `std::array<int32_t, 2>*` off the
    # storage-form optional field, so `g.bump()` is visible through it.
    xs = g.cells
    if xs is None:
        return -1
    g.bump()
    return xs[0]


def size_of(g: Grid) -> int32:
    ys = g.cells
    if ys is None:
        return 0
    return len(ys)


def main() -> None:
    g = Grid()
    print(size_of(g))
    print(first_after_bump(g))


main()
