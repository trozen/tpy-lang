# Forwarding *xs into a callee that mutates: Phase 2 mutation propagation
# follows the vararg edge from outer.xs to mutate_all's vararg slot, so outer
# keeps its slot mutable too. The wholesale source borrow needs the slot to
# stay non-const so codegen's per-element address-take into the mutable
# `varargs<Box>` pack is well-typed.
from tpy import int32


class Box:
    val: int32

    def __init__(self, v: int32) -> None:
        self.val = v


def mutate_all(*items: Box) -> None:
    for b in items:
        b.val += 10


def outer(*xs: Box) -> None:  # tpyc: ok
    mutate_all(*xs)


def main() -> None:
    a = Box(1)
    b = Box(2)
    outer(a, b)
    print(a.val)
    print(b.val)


main()
