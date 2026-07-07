# Weak[T] survives the last Arc dropping: upgrade() returns None once the
# payload has been destroyed, and the Weak handle's cell-memory access stays
# valid (the atomically-refcounted cell is freed only when the last Weak drops).
# Mirrors the Rc weak_upgrade_after_drop test on the atomic sibling.
from tpy import Int32, Own
from tplib.arc import Arc, Weak


class Cell:
    val: Int32

    def __init__(self, v: Int32) -> None:
        self.val = v

    def __del__(self) -> None:
        print("Cell.__del__", self.val)


def make_weak_after_arc_dies() -> Own[Weak[Cell]]:
    arc = Arc.new(Cell(7))
    w = arc.downgrade()
    # arc drops at function return -- payload destructed (strong 1 -> 0), but
    # `w` keeps the cell memory alive (weak still 1).
    return w


def main() -> None:
    print("--- pre ---")
    w = make_weak_after_arc_dies()
    print("--- post ---")
    upgraded = w.upgrade()
    if upgraded is None:
        print("upgrade returned None")
    else:
        print("unexpected upgrade:", upgraded.get().val)
    print("--- done ---")


main()
