# Qualified cross-module use of a generic recursive alias: `import treelib`
# then `treelib.Tree[int]` at a local annotation AND a main-side function
# parameter (exercises the finalize pass over use-site annotations and
# function signatures). Read-only traversal is intentional: runtime semantics
# are identical to the `from treelib import Tree` form, so this case targets
# the qualified-name *resolution*, not value-vs-reference behavior.
import treelib


def depth(t: treelib.Tree[int]) -> int:
    match t:
        case list() as branches:
            best = 0
            for child in branches:
                d = depth(child)
                if d > best:
                    best = d
            return best + 1
        case _:
            return 0


def main() -> None:
    t: treelib.Tree[int] = [1, [2, 3], 4]  # tpyc: type(/Tree/)
    print(treelib.leaf_count(t))
    print(depth(t))


main()
