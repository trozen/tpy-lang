# A numeric type constructor as the first binding gives the local its type, so
# a wider value stored later is refused with the annotation to write. A
# documented divergence (docs/LANGUAGE_FEATURES.md "Numeric widening across
# reassignments"): CPython rebinds.
from tpy import int8, int64


def main(wide: int64) -> None:
    x = int8(3)
    x = wide  # tpyc: error(/'x' is int8 \(line 9\) and this value is int64; annotate its first binding: x: int64 = \.\.\./)
    print(x)


main(5)
