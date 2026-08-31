# Recursive union alias instantiated with a union type argument: an arm naming
# a type INSIDE that leaf union has no wrapper variant to dispatch on.
from tpy import Int32

type Tree[T] = T | list[Tree[T]]


def describe(t: Tree[Int32 | str]) -> str:
    match t:
        case Int32():  # tpyc: error(/'Int32' is not a member of union/)
            return "int"
        case str():  # rejected the same way; sema stops at the first error
            return "str"
        case _:
            return "branch"


def main() -> None:
    a: Tree[Int32 | str] = 1
    print(describe(a))


main()
