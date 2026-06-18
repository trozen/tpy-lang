# float augmented assignment (+= -= *= /=) with fixed-width ints (incl. Int32).
# Whole-number results for CPython parity.
from tpy import Int8, Int32, Int64, UInt16


def main() -> None:
    x = 10.0
    x += Int8(5)
    x -= Int32(3)
    x *= UInt16(2)
    x /= Int64(4)
    print(x)


main()
