# Generic recursive type alias: type Tree[T] = T | list[Tree[T]]. Covers a
# generic traversal (leaf_count[T], case _ for the T leaf), a concrete-leaf
# extraction (sum_leaves on Tree[int], case int() binds the leaf value),
# construction of a nested value, printing, and equality on the wrapper.
from tpy import int32

type Tree[T] = T | list[Tree[T]]


def leaf_count[T](t: Tree[T]) -> int32:  # tpyc: ok
    match t:
        case list() as branches:
            total = 0
            for child in branches:
                total += leaf_count(child)
            return total
        case _:
            return 1


def sum_leaves(t: Tree[int]) -> int32:
    match t:
        case list() as branches:
            total = 0
            for child in branches:
                total += sum_leaves(child)
            return total
        case int() as v:
            return v


def main() -> None:
    t: Tree[int] = [1, [2, 3], 4]  # tpyc: type(Tree[int])
    print(leaf_count(t))
    print(sum_leaves(t))
    leaf: Tree[int] = 9
    print(leaf_count(leaf))
    a: Tree[int] = [1, 2]
    b: Tree[int] = [1, 2]
    print(a == b)


main()
