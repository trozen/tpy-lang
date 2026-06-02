# Wrappers follow the reference-type return convention: a fresh value must be
# returned as Own[Tree[T]] (lowers to `Tree<int32_t>` by value / move). A bare
# `-> Tree[T]` return is `Tree<int32_t>&` and is rejected for a fresh value --
# see error_generic_recursive_return_dangle.
from tpy import Int32, Own

type Tree[T] = T | list[Tree[T]]


def make_leaf() -> Own[Tree[Int32]]:
    return Int32(7)


def make_branch() -> Own[Tree[Int32]]:
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
