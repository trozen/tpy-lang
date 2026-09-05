# Array at the reference-shaped lowering slots: an Own[Array] return, an
# Optional[Array] local and return slot, an Array field compared and iterated,
# and an Array (and a bytearray) name at an all-protocols constructor slot.
# Both are reference types (value_form BORROW_REF), so each of these routes
# exactly as the list flavour beside it does.
from __future__ import annotations

from tpy import Array, Int32, Own, UInt8
from tplib import ArrayList


class Grid:
    cells: Array[Int32, 3]
    spare: Array[Int32, 3] | None

    def __init__(self) -> None:
        self.cells = [1, 2, 3]
        self.spare = [4, 5, 6]

    def same_cells(self, other: Grid) -> bool:
        return self.cells == other.cells      # the Array FIELD read as an operand

    def spare_total(self) -> Int32:
        n = 0
        if self.spare is not None:
            for x in self.spare:              # for-each over a narrowed Array field
                n += x
        return n


def fresh() -> Own[Array[Int32, 3]]:
    return [7, 8, 9]                          # the Own[Array] storage return slot


def first_or_none(g: Grid, want: bool) -> Array[Int32, 3] | None:
    if want:
        return g.cells                        # the Optional[Array] borrow return
    return None


def main() -> None:
    a = Grid()
    b = Grid()
    print(a.same_cells(b))
    b.cells[0] = 99
    print(a.same_cells(b))
    print(a.spare_total())

    made = fresh()
    print(made[0], made[2])

    got = first_or_none(a, True)              # an Optional[Array] local slot
    if got is not None:
        got[1] = 42                           # mutation through the borrow ...
    print(a.cells[1])                         # ... is visible on the source
    print(first_or_none(a, False) is None)

    xs: Array[Int32, 3] = [5, 6, 7]
    al = ArrayList[Int32, 8](xs)              # an Array NAME at a protocol slot
    print(al[0], len(al))

    ba = bytearray(b"abc")                    # ... and the bytearray leg
    bl = ArrayList[UInt8, 8](ba)
    print(bl[0], len(bl))


main()
