# Collision guard: this module defines its OWN `type Tree[T]` AND uses
# `treelib.Tree[int]` qualified. The two must stay distinct C++ wrapper types
# -- the local `Tree<...>` (bare, this module's namespace) and treelib's
# `::tpyapp::treelib::Tree<...>`. Identity is keyed by qualified name, not the
# short name, so the local alias never shadows the qualified reference and the
# renders never collide. Read-only traversal is intentional (targets resolution
# + distinct rendering, not value-vs-reference). (Int32 traversal width avoids
# the pre-existing member-template-on-dependent-receiver bug; see BUGS.md.)
import treelib
from tpy import Int32

type Tree[T] = T | list[Tree[T]]


def local_count[T](t: Tree[T]) -> Int32:
    match t:
        case list() as branches:
            total = 0
            for child in branches:
                total += local_count(child)
            return total
        case _:
            return 1


def main() -> None:
    # Both display as `Tree[int]` (the short name); their *distinct* identity
    # (and bare-vs-qualified C++ render) is pinned by the snapshot. These
    # comp-phase asserts guard that each still resolves to the wrapper rather
    # than regressing to the old mixed-types list-literal failure.
    mine: Tree[int] = [1, [2, 3], 4, [5, [6, 7]]]  # tpyc: type(/Tree\[/)
    theirs: treelib.Tree[int] = [10, [20, 30]]  # tpyc: type(/Tree\[/)
    print(local_count(mine))
    print(treelib.leaf_count(theirs))


main()
