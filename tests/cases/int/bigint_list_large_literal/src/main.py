# A BigInt list literal with a >int32 element must render that element via the
# ambiguity-safe BigInt(static_cast<int64_t>(...)) ctor, not a bare C++ `long`
# (which converts to BigInt ambiguously on macOS, where int64_t is long long).
# Small elements stay bare; a fixed-width int64 list must NOT wrap.
from tpy import int64


def main() -> None:
    xs: list[int] = [0, 10, -7, 1234567890123456789]
    for x in xs:
        print(x)

    neg: list[int] = [-1234567890123456789, 5]
    print(neg[0], neg[1])

    nested: list[list[int]] = [[1234567890123456789, 5]]
    print(nested[0][0], nested[0][1])

    # Boundary transitions of the target-less render policy: int32 edge (2^31-1
    # and -(2^31-1) stay bare, 2^31 and -2^31 wrap to int64), int64 edge
    # (static_cast<int64_t>), uint64 range (static_cast<uint64_t>), and beyond
    # uint64 / below int64-min (BigInt::from_str) in both signs.
    edges: list[int] = [2147483647, -2147483647, 2147483648, -2147483648,
                        9223372036854775807, -9223372036854775808,
                        9223372036854775808, 18446744073709551616,
                        -9223372036854775809]
    for x in edges:
        print(x)

    # BigInt-TARGET boundaries (a typed init routes the is_big_int arm, distinct
    # from the target-less list elements above): INT64_MIN takes the (-MAX-1)
    # spelling, and a value past int64 takes from_str.
    tmin: int = -9223372036854775808
    tbig: int = 18446744073709551616
    print(tmin, tbig)

    # Inverse: a fixed-width int64 list stays a plain integer brace-init (no
    # BigInt wrap), which the fix must not disturb.
    fixed: list[int64] = [1234567890123456789, 5]
    print(fixed[0], fixed[1])


main()
