# A fresh recursive-union-wrapper member of a tuple LOCAL, returned by bare name,
# dangles (the element is borrow form), so it must be rejected -- pointing at Own[].
from tpy import int32

type Tree[T] = T | list[Tree[T]]


def f() -> tuple[Tree[int32], int32]:
    leaf: Tree[int32] = 5
    pair = (leaf, 0)
    return pair  # tpyc: error(/owns a freshly constructed value/)


def main() -> None:
    pass


main()
