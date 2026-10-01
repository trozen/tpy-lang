# A float value stored into an annotated float32 local is refused, as a wider
# int value into an annotated int32 is; the fix names the annotation.
# (A documented restriction: docs/LANGUAGE_FEATURES.md, "Numeric widening
# across reassignments".)
from tpy import float32


def main(wide: float) -> None:
    x: float32 = 1.5
    # the float value into the annotated float32 local
    x = wide  # tpyc: error(/'x' is declared float32 \(line 9\) and this value is float; write float32\(wide\) to narrow it, or annotate it float there: x: float = \.\.\./)
    print(x)


main(2.5)
