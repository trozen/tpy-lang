# An augmented assignment whose float result is wider than the float32 a
# constructor first binding gave the local is refused, as an int64 result
# into an int32 local is.
# (A documented restriction: docs/LANGUAGE_FEATURES.md, "Numeric widening
# across reassignments".)
from tpy import float32


def main(wide: float) -> None:
    x = float32(1.5)
    # the float product into the float32 local
    x *= wide  # tpyc: error(/'x' is float32 \(line 10\) and 'x \*= wide' produces float; annotate its first binding: x: float = \.\.\./)
    print(x)


main(2.5)
