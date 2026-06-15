# Optional narrowing of a generic recursive alias instance: a `Tree[int] | None`
# parameter narrowed via `is None`. Confirms the RecursiveAliasInstanceType is
# recognized by the Optional/None narrowing path (the non-generic analogue is
# union_recursive_optional) and that a `Tree[int]` widens into the optional
# param. Read-only traversal is intentional -- targets narrowing, not
# value-vs-reference.
from treelib import Tree, leaf_count


def maybe_count(t: Tree[int] | None) -> int:
    if t is None:
        return -1
    return leaf_count(t)


def main() -> None:
    t: Tree[int] = [1, [2, 3], 4]
    print(maybe_count(t))
    print(maybe_count(None))


main()
