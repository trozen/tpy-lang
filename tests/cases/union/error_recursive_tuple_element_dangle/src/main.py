# A fresh value coerced into a wrapper tuple element (borrow form) is a
# dangling temporary -- sema requires Own[Tree[Int32]] for the element.
from tpy import Int32

type Tree[T] = T | list[Tree[T]]


def bad() -> tuple[Tree[Int32], Int32]:
    return (1, 0)  # tpyc: error(/tuple element 0.*Use Own\[Tree\[Int32\]\]/)


def main() -> None:
    bad()


main()
