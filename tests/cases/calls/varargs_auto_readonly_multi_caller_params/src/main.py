# Three distinct caller params flowing into the same mutating vararg slot
# exercises the extra_vararg_edges append path in _record_mutation_call_edges:
# the first arg lands in the main param_map entry, the rest spawn standalone
# MutationCallEdges. Phase 2 propagation must mark all three caller params
# (a, b, c) non-const so the address-take into the indirect mutable pack works.
from tpy import int32


class Box:
    val: int32

    def __init__(self, v: int32) -> None:
        self.val = v


def bump_all(*items: Box) -> None:
    for b in items:
        b.val += 1


def via_three(a: Box, b: Box, c: Box) -> None:  # tpyc: ok
    bump_all(a, b, c)


def main() -> None:
    x = Box(1)
    y = Box(2)
    z = Box(3)
    via_three(x, y, z)
    print(x.val)
    print(y.val)
    print(z.val)


main()
