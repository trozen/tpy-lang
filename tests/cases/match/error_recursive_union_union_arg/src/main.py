# Recursive union alias instantiated with a union type argument, matched by
# class pattern. Pins the current codegen-side rejection of that shape.
from tpy import Int32

type Tree[T] = T | list[Tree[T]]


def describe(t: Tree[Int32 | str]) -> str:
    match t:
        case Int32():  # tpyc: error(/type 'Int32' not found in union/)
            return "int"
        case str():
            return "str"
        case _:
            return "branch"


def main() -> None:
    a: Tree[Int32 | str] = 1
    print(describe(a))


main()
