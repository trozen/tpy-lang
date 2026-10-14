# An int literal int32 cannot hold leaves no match, though CPython runs h(int32):
# docs/LANGUAGE_FEATURES.md "Overloading" -- an out-of-range literal is no conversion.
from tpy import dispatch, int8, int32, int64


@dispatch
def h(x: int32, n: int64) -> str:
    return "int32"


@dispatch
def h(x: str, n: int64) -> str:
    return "str"


def main() -> None:
    n8: int8 = 1
    print(h(10000000000, n8))  # tpyc: error(/No matching overload for h\(int, int8\)/)


main()
