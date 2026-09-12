# print() output forms -- string literal, fixed-int, bool, double, and an 8-bit
# int (cast so it prints as a number, not a char) -- plus an empty print and a
# bare free-function call statement (evaluated for its side effect). Value-only,
# so the output byte-compare against CPython is a full parity check.
from tpy import int32, uint8


def show(n: int32, ok: bool, ratio: float, small: uint8) -> None:
    print("n =", n)
    print(n, ok, ratio)
    print(small)          # 8-bit -> static_cast<int>, prints as a number
    print()
    banner()              # a bare call statement


def banner() -> None:
    print("----")


def main() -> None:
    show(42, True, 2.5, 200)
    show(-7, False, 0.0, 0)


main()
