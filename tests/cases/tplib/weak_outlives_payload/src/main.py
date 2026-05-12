# Multiple Weak handles outlive the payload. The payload destructs when the
# last Rc drops; cell-backing memory stays alive until the last Weak drops,
# so every surviving Weak.upgrade() reports None correctly.
from tpy import Int32, Own
from tplib import Rc, Weak, make_rc


class Cell:
    val: Int32

    def __init__(self, v: Int32) -> None:
        self.val = v

    def __del__(self) -> None:
        print("payload destruct")


def make_weaks() -> Own[tuple[Weak[Cell], Weak[Cell], Weak[Cell]]]:
    # Build three Weaks; the strong Rc dies at this function's return so the
    # payload destructs before the weaks are returned to the caller.
    rc = make_rc(Cell(Int32(42)))
    w1 = rc.downgrade()
    w2 = w1.clone()
    w3 = w2.clone()
    print("alive before drop:", rc.get().val)
    return (w1, w2, w3)


def main() -> None:
    weaks = make_weaks()
    # By this point the payload has been destroyed (last strong dropped at
    # make_weaks's exit). Cell memory stays valid via the three Weaks.
    print("--- after strong death ---")
    print("w1:", weaks[0].upgrade() is None)
    print("w2:", weaks[1].upgrade() is None)
    print("w3:", weaks[2].upgrade() is None)

    # weaks tuple drops at function return; cell memory freed at the last
    # weak's destruction.


main()
