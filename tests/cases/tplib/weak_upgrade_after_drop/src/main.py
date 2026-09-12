# Weak[T] survives the last Rc dropping: upgrade() returns None once the
# payload has been destroyed; the Weak handle's cell-memory access stays
# valid (cell is freed only when the last Weak drops).
from tpy import int32, Own
from tplib.rc import Rc, Weak


class Cell:
    val: int32

    def __init__(self, v: int32) -> None:
        self.val = v

    def __del__(self) -> None:
        print("Cell.__del__", self.val)


def make_weak_after_rc_dies() -> Own[Weak[Cell]]:
    rc = Rc.new(Cell(int32(7)))
    w = rc.downgrade()
    # rc drops at function return -- payload destructed, but `w` keeps the
    # cell memory alive (strong=0, weak=1).
    return w


def main() -> None:
    print("--- pre ---")
    w = make_weak_after_rc_dies()
    print("--- post ---")
    upgraded = w.upgrade()
    if upgraded is None:
        print("upgrade returned None")
    else:
        print("unexpected upgrade:", upgraded.get().val)
    print("--- done ---")


main()
