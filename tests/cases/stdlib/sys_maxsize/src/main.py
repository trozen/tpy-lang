# sys.maxsize is CPython's 64-bit value (2**63 - 1) and usable as a
# regular int (BigInt) in arithmetic and comparisons.
import sys


def main() -> None:
    print(sys.maxsize)
    print(sys.maxsize == 2**63 - 1)
    print(sys.maxsize + 1)
    n = 5
    print(n < sys.maxsize)


main()
