# A record in `main` embeds a cross-module generic recursive alias by value
# (`canopy: treelib.Tree[int]`) and reads it via a method. Guards the emit
# ordering: the imported wrapper template (treelib's `.hpp`) must be complete
# before this module's record embeds `Tree<BigInt>` by value. The field is
# built from a fresh literal in __init__ (no cross-boundary move), so this is a
# read-only / fresh-construction test, not a value-vs-reference one.
from treelib import Tree, leaf_count


class Forest:
    canopy: Tree[int]

    def __init__(self) -> None:
        self.canopy = [1, [2, 3], 4, [5, [6, 7]]]

    def size(self) -> int:
        return leaf_count(self.canopy)


def main() -> None:
    f = Forest()
    print(f.size())


main()
