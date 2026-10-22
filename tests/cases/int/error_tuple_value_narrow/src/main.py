# The operator twin of divmod(): a tuple VARIABLE of promoted results into a
# narrower tuple slot is refused (BUGS.md#tuple-value-element-narrowing).
from tpy import int8, uint8


def main() -> None:
    a8 = int8(-7)
    b8 = int8(2)
    t = (a8 // b8, a8 % b8)
    q2: tuple[uint8, uint8] = t  # tpyc: error(/a tuple element is int32, which uint8 does not hold every value of/)
    print(q2)


main()
