# A positional-only param cannot be passed by keyword (CPython TypeError).
from tpy import Int32


def f(a: Int32, /, b: Int32) -> Int32:
    return a + b


def main() -> None:
    print(f(a=1, b=2))  # tpyc: error(/parameter 'a' is positional-only/)


main()
