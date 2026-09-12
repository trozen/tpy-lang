# float augmented assignment (+= -= *= /=) with fixed-width ints (incl. int32).
# Whole-number results for CPython parity.
from tpy import int8, int32, int64, uint16


def main() -> None:
    x = 10.0
    x += int8(5)
    x -= int32(3)
    x *= uint16(2)
    x /= int64(4)
    print(x)


main()
