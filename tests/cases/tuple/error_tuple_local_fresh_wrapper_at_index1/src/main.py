# A fresh recursive-union-wrapper member at a NON-zero tuple index (element 1)
# must be rejected -- guards the per-element index computation in the dangle check.
from tpy import int32

type Tree[T] = T | list[Tree[T]]


def f() -> tuple[int32, Tree[int32]]:
    leaf: Tree[int32] = 5
    pair = (0, leaf)
    return pair  # tpyc: error(/owns a freshly constructed value/)


def main() -> None:
    pass


main()
