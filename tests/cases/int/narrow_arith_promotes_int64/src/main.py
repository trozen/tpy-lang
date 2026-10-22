# Under --default-int=int64 an 8/16-bit operator computes in int64, pow() and
# divmod() included.
from tpy import int8, uint16


def main() -> None:
    a8 = int8(100)
    u16 = uint16(60000)
    s = a8 + a8  # tpyc: type(int64)
    p = u16 * u16 * u16  # tpyc: type(int64)
    neg = -u16  # tpyc: type(int64)
    low = u16 & 0xFF  # tpyc: type(uint16)
    pw = pow(a8, int8(4))  # tpyc: type(int64)
    q, r = divmod(int8(-7), int8(2))
    print(s, p, neg, low, pw, q, r)


main()
