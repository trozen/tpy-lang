# User-level `def super()` shadows the builtin at call sites (CPython parity).
# super() is a @builtin_type class resolved through the normal namespace chain;
# it is not a keyword or syntactic intercept.
from tpy import int32


def super() -> int32:
    return int32(42)


def main() -> None:
    x = super()
    print(x)


main()
