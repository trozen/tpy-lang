# Weak[T] basic downgrade -> upgrade cycle. While at least one Rc is alive,
# upgrade() succeeds and returns a new Rc[T] sharing the same allocation;
# mutation through the upgraded Rc is visible to the original.
from tpy import Int32
from tplib import Rc, Weak, make_rc


class Cell:
    val: Int32

    def __init__(self, v: Int32) -> None:
        self.val = v


def main() -> None:
    rc = make_rc(Cell(Int32(10)))
    w = rc.downgrade()
    print(rc.get().val)  # 10

    rc2 = w.upgrade()
    assert rc2 is not None
    print(rc2.get().val)  # 10

    rc2.get().val = Int32(99)
    print(rc.get().val)  # 99 -- shared


main()
