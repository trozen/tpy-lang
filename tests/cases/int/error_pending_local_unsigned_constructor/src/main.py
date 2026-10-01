# A numeric type constructor is a value like any other: stored into a
# literal-seeded local it is a uint32 value, which has no common type with
# the default int32 the literal gives, so the local never becomes unsigned.
# (A documented restriction: docs/LANGUAGE_FEATURES.md, "Numeric widening
# across reassignments".)
from tpy import uint32


def main() -> None:
    x = 0
    # the uint32 value stored into the literal-seeded local
    x = uint32(1)  # tpyc: error(/'x' holds int32 values and this value is uint32, which have no common type; annotate its first binding: x: uint32 = 0/)
    print(x)


main()
