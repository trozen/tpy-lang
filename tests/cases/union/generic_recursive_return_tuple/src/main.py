# Generic recursive alias as a tuple element of a return type:
# `tuple[Tree[Int32], Int32]` must not lower the wrapper element to
# `Tree<int32_t>&` (which would dangle for a function-local return).
from tpy import Int32

type Tree[T] = T | list[Tree[T]]


def make_pair() -> tuple[Tree[Int32], Int32]:
    leaf: Tree[Int32] = [1, 2]
    return (leaf, 0)


def main() -> None:
    t, n = make_pair()
    print(n)


main()
