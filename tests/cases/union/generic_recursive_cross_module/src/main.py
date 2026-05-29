# A generic recursive alias (and a traversal over it) defined in treelib,
# used from main: the wrapper template is emitted in the defining module's
# header and instantiated cross-module. Construct a value here and dispatch
# through the imported function.
from treelib import Tree, leaf_count


def main() -> None:
    t: Tree[int] = [1, [2, 3], 4]
    print(leaf_count(t))
    leaf: Tree[int] = 7
    print(leaf_count(leaf))


main()
