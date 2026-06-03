# A fresh recursive-union-wrapper member of a tuple LOCAL, returned by bare name,
# dangles (the element is borrow form), so it must be rejected -- pointing at Own[].
from tpy import Int32

type Tree[T] = T | list[Tree[T]]


def f() -> tuple[Tree[Int32], Int32]:
    leaf: Tree[Int32] = 5
    pair = (leaf, 0)
    return pair  # tpyc: error(/owns a freshly constructed value/)


def main() -> None:
    pass


main()
