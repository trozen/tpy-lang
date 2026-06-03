# A fresh recursive-union-wrapper member at a NON-zero tuple index (element 1)
# must be rejected -- guards the per-element index computation in the dangle check.
from tpy import Int32

type Tree[T] = T | list[Tree[T]]


def f() -> tuple[Int32, Tree[Int32]]:
    leaf: Tree[Int32] = 5
    pair = (0, leaf)
    return pair  # tpyc: error(/owns a freshly constructed value/)


def main() -> None:
    pass


main()
