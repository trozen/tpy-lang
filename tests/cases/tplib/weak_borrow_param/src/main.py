# Weak[T] passed as a plain borrow param (no Own[]). Mirrors the
# rc_refcount_drop's use(r: Rc[State]) pattern for Weak: a callee that
# inspects a Weak without taking ownership. Verifies the borrow-form
# codegen path (passing Weak by reference, not by move).
from tpy import Int32
from tplib.rc import Rc, Weak


class Cell:
    val: Int32

    def __init__(self, v: Int32) -> None:
        self.val = v


def read_via_weak(w: Weak[Cell]) -> Int32:
    upgraded = w.upgrade()
    if upgraded is None:
        return Int32(-1)
    return upgraded.get().val


def main() -> None:
    rc = Rc.new(Cell(Int32(7)))
    w = rc.downgrade()

    print(read_via_weak(w))  # 7 (w borrowed; caller retains ownership)
    w2 = w.clone()
    print(read_via_weak(w2))  # 7


main()
