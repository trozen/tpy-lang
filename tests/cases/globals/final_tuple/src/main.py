# Final[tuple[...]] emits extern const in header, const init in source.
# Covers plain literals and binary ops on elements.
from typing import Final
from tpy import Int32

VERSION: Final[tuple[Int32, Int32, Int32]] = (1, 2, 3)
PAIR: Final[tuple[str, bool]] = ("hello", True)
ARITH: Final[tuple[Int32, Int32]] = (10 + 20, 100 - 1)

def main() -> None:
    major, minor, patch = VERSION
    print(major)
    print(minor)
    print(patch)
    label, flag = PAIR
    print(label)
    print(flag)
    a, b = ARITH
    print(a)
    print(b)

main()
