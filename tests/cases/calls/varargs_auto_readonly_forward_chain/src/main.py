# Three-deep forwarding chain: top -> mid -> leaf, where only leaf mutates.
# Propagation must reach every level via the vararg edges so the entire chain
# keeps its slot mutable; the chain converges in the Phase 2 fixpoint.
from tpy import int32


class Box:
    val: int32

    def __init__(self, v: int32) -> None:
        self.val = v


def leaf(*items: Box) -> None:
    for b in items:
        b.val *= 2


def mid(*items: Box) -> None:
    leaf(*items)


def top(*items: Box) -> None:  # tpyc: ok
    mid(*items)


def main() -> None:
    a = Box(3)
    b = Box(5)
    top(a, b)
    print(a.val)
    print(b.val)


main()
