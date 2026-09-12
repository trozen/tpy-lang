# A positional-only param cannot be passed by keyword (CPython TypeError).
from tpy import int32


def f(a: int32, /, b: int32) -> int32:
    return a + b


def main() -> None:
    print(f(a=1, b=2))  # tpyc: error(/parameter 'a' is positional-only/)


main()
