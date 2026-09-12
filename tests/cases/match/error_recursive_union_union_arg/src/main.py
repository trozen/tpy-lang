# Recursive union alias instantiated with a union type argument: an arm naming
# a type INSIDE that leaf union has no wrapper variant to dispatch on.
# A capability gap, not a rule -- CPython runs this; see
# BUGS.md#recursive-alias-leaf-union-member-unnameable.
from tpy import int32

type Tree[T] = T | list[Tree[T]]


def describe(t: Tree[int32 | str]) -> str:
    match t:
        case int32():  # tpyc: error(/'int32' is not a member of union/)
            return "int"
        case str():  # rejected the same way; sema stops at the first error
            return "str"
        case _:
            return "branch"


def main() -> None:
    a: Tree[int32 | str] = 1
    print(describe(a))


main()
