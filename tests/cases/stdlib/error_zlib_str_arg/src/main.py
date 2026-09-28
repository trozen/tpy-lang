# zlib takes bytes-like data: a str argument is rejected at compile time
# (CPython raises TypeError at run time).
import zlib


def main() -> None:
    print(zlib.crc32("text"))  # tpyc: error(/expected bytes, got str/)


main()
