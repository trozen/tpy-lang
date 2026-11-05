# chr(n) / char(n) outside 0..127 raise ValueError (OverflowError beyond
# int32).
from tpy import char, int32


def out_of_range(n: int32) -> None:
    try:
        # chr at int32 outside the ASCII range.
        c = chr(n)  # tpyc: ok
        print("chr:", n, repr(c))
    except ValueError as e:
        print("chr:", n, "ValueError:", e)


def ctor_out_of_range() -> None:
    n = 200
    try:
        # char(n) constructor outside the ASCII range.
        c = char(n)  # tpyc: ok
        print("ctor:", repr(c))
    except ValueError as e:
        print("ctor: ValueError:", e)


def beyond_int32() -> None:
    try:
        # chr at int beyond int32.
        c = chr(10**20)  # tpyc: ok
        print("big:", repr(c))
    except OverflowError:
        print("big: OverflowError")


def main() -> None:
    out_of_range(128)
    out_of_range(200)
    out_of_range(-1)
    out_of_range(9608)
    ctor_out_of_range()
    beyond_int32()


main()
