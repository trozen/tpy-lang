# divmod() of 8/16-bit operands returns a tuple of the default int, which a
# narrower tuple slot refuses (BUGS.md#tuple-value-element-narrowing).
from tpy import int8, uint8


def main() -> None:
    q: tuple[uint8, uint8] = divmod(int8(-7), int8(2))  # tpyc: error(/a tuple element is int32, which uint8 does not hold every value of/)
    print(q)


main()
