# Aliased cross-module import of a generic recursive alias:
# `from treelib import Tree as T2`, then `T2[int]` at use sites. The use site
# must resolve under the local spelling `T2`, and the wrapper body's
# self-reference -- which carries treelib's own short name `Tree` -- must still
# render the same qualified C++ wrapper as the use-site instance. Read-only
# traversal is intentional: this targets resolution, not value-vs-reference.
from treelib import Tree as T2, leaf_count


def main() -> None:
    t: T2[int] = [1, [2, 3], 4]  # tpyc: type(/T2/)
    print(leaf_count(t))
    leaf: T2[int] = 7
    print(leaf_count(leaf))


main()
