# Final[tuple[...]] emits extern const in header, const init in source.
# Covers plain literals, binary ops on elements, and nested tuples with str.
from typing import Final
from tpy import Int32

VERSION: Final[tuple[Int32, Int32, Int32]] = (1, 2, 3)
PAIR: Final[tuple[str, bool]] = ("hello", True)
ARITH: Final[tuple[Int32, Int32]] = (10 + 20, 100 - 1)
NESTED: Final[tuple[tuple[str, Int32], str]] = (("inner", 42), "outer")

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
    inner, outer = NESTED
    name, val = inner
    print(name)
    print(val)
    print(outer)

main()
