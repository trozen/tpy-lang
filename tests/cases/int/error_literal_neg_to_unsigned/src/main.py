# Negative integer literal at an unsigned-target call site is rejected
# at compile time with a range error -- the literal value is known.
from tpy import uint64


def f(x: uint64) -> None:
    pass


def main() -> None:
    f(-1)   # tpyc: error(/-1 is outside uint64 range/)


main()
