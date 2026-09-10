# A value union renders as ::tpy::Union (a std::variant that owns Python's
# comparison rule); the None-carrying one adds the monostate member. The
# compares stay within ONE alternative: the cross-alternative answers are
# `tests/cases/union/value_union_cross_alternative_eq`, so what this case pins
# is the RENDER -- the bare `(a == b)` on the TPy type, not a helper call and
# not the variant's own index-first operator.
from tpy import Float64, Int32


def same(a: Int32 | Float64, b: Int32 | Float64) -> bool:
    return a == b  # a variant-vs-variant compare


def widened() -> bool:
    x: Int32 | Float64 = 1  # std::variant<int32_t, double>
    x = 2.5
    y = x
    return same(y, 2.5)


def started_none() -> bool:
    x: Int32 | Float64 | None = None  # the monostate member
    was_none = x is None
    x = 3
    return was_none and x is not None


def main() -> None:
    print(same(1, 1), same(1, 2))
    print(widened())
    print(started_none())


main()
