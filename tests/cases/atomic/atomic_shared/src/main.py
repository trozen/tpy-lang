# Atomic is a shared mutable cell: a mutation through one handle is visible
# through another that aliases the same cell (reference semantics, the whole
# point of the type). We share one Atomic by embedding it in an Arc-managed
# Counter and cloning the Arc; Atomic is @nocopy, so a silent copy anywhere
# would be a compile error rather than a hidden second cell.
from tpy import uint32
from tplib.arc import Arc
from tpy.atomic import Atomic, MemoryOrder


class Counter:
    n: Atomic[uint32]

    def __init__(self, start: uint32) -> None:
        self.n = Atomic[uint32](start)


def main() -> None:
    a = Arc.new(Counter(0))
    b = a.clone()              # b aliases a's Counter -- the same atomic cell
    a.get().n += 5             # mutate through a (in-place atomic RMW)
    a.get().n.fetch_add(3)     # -> 8
    print(b.get().n.load())    # 8 -- observed through b
    b.get().n.store(100, MemoryOrder.RELAXED)
    print(a.get().n.load())    # 100 -- and visible back through a


main()
