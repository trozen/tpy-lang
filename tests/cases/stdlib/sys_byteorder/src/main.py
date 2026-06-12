# sys.byteorder is the platform endianness string; sys.maxunicode is the
# largest Unicode code point. Both usable as ordinary str/int values.
import sys


def main() -> None:
    bo = sys.byteorder  # tpyc: type(StrView)
    print(bo)
    print(bo == "little")
    print(sys.maxunicode)
    print(sys.maxunicode == 0x10FFFF)
    print(sys.maxunicode + 1)
    n = 32
    print(n < sys.maxunicode)


main()
