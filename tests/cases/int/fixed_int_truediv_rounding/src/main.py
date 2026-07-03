# Int64/UInt64 true division above 2^53 must be correctly rounded; below 2^53
# both operands convert exactly, so the plain double divide already is.
from tpy import Int64, UInt64


def main() -> None:
    a = Int64((1 << 60) + (1 << 7))
    b = Int64(3)
    print(a / b)              # 3.843071682022824e+17

    c = UInt64((1 << 63) + 1)
    d = UInt64(1)
    print(c / d)              # 9.223372036854776e+18

    small = Int64(100)
    seven = Int64(7)
    print(small / seven)      # fast path, unchanged


main()
