# `return None` in a `-> int32` function is a return-type mismatch; the bare
# `return` spelling is the same statement and gets the same diagnostic.
from tpy import int32


def f() -> int32:
    return None  # tpyc: error(/Type mismatch in return value: expected int32, got None/)


def main() -> None:
    print(f())


main()
