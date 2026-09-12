# int64/uint64 true division above 2^53 must be correctly rounded; below 2^53
# both operands convert exactly, so the plain double divide already is.
from tpy import int64, uint64


def main() -> None:
    a = int64((1 << 60) + (1 << 7))
    b = int64(3)
    print(a / b)              # 3.843071682022824e+17

    c = uint64((1 << 63) + 1)
    d = uint64(1)
    print(c / d)              # 9.223372036854776e+18

    small = int64(100)
    seven = int64(7)
    print(small / seven)      # fast path, unchanged


main()
