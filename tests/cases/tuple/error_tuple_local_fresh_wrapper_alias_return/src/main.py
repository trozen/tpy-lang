# The FRESH hazard also propagates through an alias: a fresh recursive-union
# wrapper member of a tuple local, aliased then returned by name, dangles like
# the direct form and is rejected pointing at Own[].
from tpy import Int32

type Tree[T] = T | list[Tree[T]]


def f() -> tuple[Tree[Int32], Int32]:
    leaf: Tree[Int32] = [7, 8, 9]
    t = (leaf, 0)
    r = t
    return r  # tpyc: error(/owns a freshly constructed value/)


def main() -> None:
    pass


main()
