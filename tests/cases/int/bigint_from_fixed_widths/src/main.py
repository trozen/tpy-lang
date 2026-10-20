# A fixed-width integer of any width widens into `int` (BigInt), including
# uint32, which has no exact BigInt constructor. (The macOS size_t index of an
# Array comprehension's int counter is pinned by int_const_fold_sinks_bigint.)
from tpy import int8, int64, uint16, uint32, uint64


def widen(a: uint32, b: uint64, c: int64, d: int8, e: uint16) -> None:
    # uint32 had no exact BigInt constructor on any platform
    x: int = a  # tpyc: ok
    y: int = b
    z: int = c
    w: int = d
    v: int = e
    print("widen", x, y, z, w, v)


def compare(n: int, a: uint32) -> None:
    # The BigInt side of a mixed comparison wraps the uint32 operand
    print("compare", n == a, n < a, a >= n)  # tpyc: ok


def main() -> None:
    widen(4000000000, 18446744073709551615, -9223372036854775807, -128, 65535)
    compare(4000000000, 4000000000)
    compare(-1, 0)


main()
