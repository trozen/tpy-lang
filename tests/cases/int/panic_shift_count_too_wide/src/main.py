# A shift count is range-checked in its own type before use: an int64 count
# of 256 past an int8's width panics rather than wrap to a shift by 0.
from tpy import int8, int64


def main() -> None:
    x = int8(1)
    k = int64(256)
    print(x << k)  # tpyc: ok


main()
