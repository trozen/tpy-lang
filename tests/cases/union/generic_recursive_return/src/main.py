# Regression guard: matches the wrapper return convention used by the
# non-generic recursive alias case (`type Expr = Lit | BinOp` -- see
# `union_mutual_basic`). Lowers to `Tree<int32_t>` by value, not
# `Tree<int32_t>&` (which would dangle on a function-local return).
# Whether wrappers SHOULD follow this convention (vs the bare-reference-type
# convention that requires `Own[]` for fresh returns) is a follow-up; see
# the "Recursive-union wrapper return convention" TODO.md entry.
from tpy import Int32

type Tree[T] = T | list[Tree[T]]


def make_leaf() -> Tree[Int32]:
    return Int32(7)


def make_branch() -> Tree[Int32]:
    return [Int32(1), Int32(2), Int32(3)]


def leaf_count(t: Tree[Int32]) -> Int32:
    match t:
        case list() as branches:
            total = 0
            for child in branches:
                total += leaf_count(child)
            return total
        case _:
            return 1


def main() -> None:
    print(leaf_count(make_leaf()))
    print(leaf_count(make_branch()))


main()
