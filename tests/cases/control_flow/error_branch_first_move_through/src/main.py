# The branch-first hoist of a MOVE-THROUGH alias -- `q = p1` in one arm, where
# sema consumes `p1` at that last use -- still rejects: the hoisted predecl is
# an `std::optional<T>` whose branch write is the rvalue-reseat arm, and a name
# source moving into it has no row there. The const-borrow and optional
# flavours of the same predecl compile (readonly/branch_first_const_borrow).
# BUGS.md#branch-first-move-through-alias-rejected.
from tpy import int32


class Point:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


def pick(c: bool) -> int32:
    p1 = Point(1)
    if c:  # tpyc: error(/not yet supported/)
        q = p1
    else:
        return 0
    return q.x


def main() -> None:
    print(pick(True))


main()
