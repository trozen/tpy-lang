# A float value wider than the float32 a constructor first binding gave the
# local is refused, as a wider int value is: the store would round it
# silently. A documented divergence (docs/LANGUAGE_FEATURES.md "Numeric
# widening across reassignments"): CPython rebinds.
from tpy import float32


def main(wide: float) -> None:
    x = float32(1.5)
    # the float value into the float32 local
    x = wide  # tpyc: error(/'x' is float32 \(line 9\) and this value is float; annotate its first binding: x: float = \.\.\./)
    print(x)


main(2.5)
