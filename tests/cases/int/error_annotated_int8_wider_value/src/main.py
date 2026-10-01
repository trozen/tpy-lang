# An annotation declares the local, so a wider value stored later -- a
# constructor call is a value like any other -- is refused, naming the
# annotation's line; CPython ignores the annotation.
# (A documented restriction: docs/LANGUAGE_FEATURES.md, "Numeric widening
# across reassignments".)
from tpy import int8, int16


def main() -> None:
    x: int8 = 0
    # the int16 value into the annotated int8 local
    x = int16(3)  # tpyc: error(/'x' is declared int8 \(line 10\) and this value is int16; annotate it int16 there/)
    print(x)


main()
