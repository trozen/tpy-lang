# A generic recursive-alias wrapper follows the reference-type return
# convention: a bare `-> Tree[T]` return is `Tree<T>&`, so returning a fresh
# (coerced / function-local) value would dangle. Sema requires Own[Tree[T]].
from tpy import int32

type Tree[T] = T | list[Tree[T]]


def make_branch() -> Tree[int32]:
    return [1, 2, 3]  # tpyc: error(/returned by reference.*Use Own\[Tree\[int32\]\]/)


def main() -> None:
    make_branch()


main()
