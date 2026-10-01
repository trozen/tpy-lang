# An augmented assignment whose result is wider than the type a constructor
# first binding gave the local is refused, as for any value-seeded local
# (docs/LANGUAGE_FEATURES.md "Cross-width FixedInt aug-assign"; CPython's int
# grows).
from tpy import int8, int64


def main(wide: int64) -> None:
    x = int8(3)
    x += wide  # tpyc: error(/'x' is int8 \(line 9\) and 'x \+= wide' produces int64; annotate its first binding: x: int64 = \.\.\./)
    print(x)


main(5)
