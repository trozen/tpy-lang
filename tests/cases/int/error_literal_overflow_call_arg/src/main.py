# Integer literal that doesn't fit the fixed-width target is rejected
# at compile time at the call site (not deferred to runtime).
from tpy import uint8


def f(x: uint8) -> None:
    pass


def main() -> None:
    f(300)   # tpyc: error(/300 is outside uint8 range/)


main()
