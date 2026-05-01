# Integer literal that doesn't fit the fixed-width target is rejected
# at compile time at the call site (not deferred to runtime).
from tpy import UInt8


def f(x: UInt8) -> None:
    pass


def main() -> None:
    f(300)   # tpyc: error(/300 is outside UInt8 range/)


main()
