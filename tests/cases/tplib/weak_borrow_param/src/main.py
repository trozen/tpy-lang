# Weak[T] passed as a plain borrow param (no Own[]). Mirrors the
# rc_refcount_drop's use(r: Rc[State]) pattern for Weak: a callee that
# inspects a Weak without taking ownership. Verifies the borrow-form
# codegen path (passing Weak by reference, not by move).
from tpy import int32
from tplib.rc import Rc, Weak


class Cell:
    val: int32

    def __init__(self, v: int32) -> None:
        self.val = v


def read_via_weak(w: Weak[Cell]) -> int32:
    upgraded = w.upgrade()
    if upgraded is None:
        return int32(-1)
    return upgraded.get().val


def main() -> None:
    rc = Rc.new(Cell(int32(7)))
    w = rc.downgrade()

    print(read_via_weak(w))  # 7 (w borrowed; caller retains ownership)
    w2 = w.clone()
    print(read_via_weak(w2))  # 7


main()
