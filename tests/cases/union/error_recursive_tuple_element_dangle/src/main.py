# A fresh value coerced into a wrapper tuple element (borrow form) is a
# dangling temporary -- sema requires Own[Tree[int32]] for the element.
from tpy import int32

type Tree[T] = T | list[Tree[T]]


def bad() -> tuple[Tree[int32], int32]:
    return (1, 0)  # tpyc: error(/tuple element 0.*Use Own\[Tree\[int32\]\]/)


def main() -> None:
    bad()


main()
