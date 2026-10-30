# A str view at a value union's owned `str` member would be copied unseen,
# so it is refused (BUGS.md#value-union-str-view-insert).
from tpy import int32


def g(u: str | int32) -> int32:
    return 1


def plain(s: str) -> int32:
    return g(s)  # tpyc: error(/call\.arg_shape\.union/)


def main() -> None:
    print(plain("x"))


main()
