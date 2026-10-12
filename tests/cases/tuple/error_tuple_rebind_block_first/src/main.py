# A rebound pointer tuple's first binding inside a block, unhoisted, whatever
# its value: its element slots would die with the block while an alias of an
# element outlives it, so the binding is refused
# (BUGS.md#ptr-tuple-block-storage-rejects).
from tpy import Own, int32


class Box:
    def __init__(self, n: int32) -> None:
        self.n = n


def make_mixed(n: int32, b: Box) -> tuple[Own[Box], Box]:
    return (Box(n), b)


def block_literal(v: Box, c: bool) -> None:
    a = v
    if c:
        # the literal's fresh element would be a slot of the `if` block
        p = (Box(1), v)  # tpyc: error(/decl.branch_slot_type/)
        a = p[0]
        p = make_mixed(9, v)
        p[1].n += 1
    a.n += 100
    print(a.n)


block_literal(Box(0), True)
