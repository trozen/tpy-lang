# An explicit type argument the ranking cannot infer from the arguments
# (`two[float](1, 1.5, n)`: T takes an int and a float) leaves the pick to
# the generic variants a trial commit validates, ranked at the substitutions
# they validated with -- the same winner in either declaration order.
# Compiler-only: CPython cannot subscript a function (`two[float]`).
from tpy import dispatch, int32, int64


@dispatch
def two[T](a: T, b: T, n: int64) -> str:
    return "int64"


@dispatch
def two[T](a: T, b: T, n: int32) -> str:
    return "int32"


@dispatch
def owt[T](a: T, b: T, n: int32) -> str:
    return "int32"


@dispatch
def owt[T](a: T, b: T, n: int64) -> str:
    return "int64"


def main() -> None:
    # the literal 1 is at home in int32
    print("declared order", two[float](1, 1.5, 1))  # tpyc: ok
    print("reversed order", owt[float](1, 1.5, 1))  # tpyc: ok


main()
