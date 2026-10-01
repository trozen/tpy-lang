# Typed sibling arms join into one type for the local; int8 and uint32 have
# none, so the arms are refused naming both lines.
# (A documented restriction: docs/LANGUAGE_FEATURES.md, "Numeric widening
# across reassignments".)
from tpy import int8, uint32


def main(c: bool) -> None:
    if c:
        x = int8(3)
    else:
        # the uint32 arm beside the int8 one
        x = uint32(4)  # tpyc: error(/'x' is int8 in one arm \(line 10\) and uint32 in the other \(line 13\), which have no common type; annotate x: int64 at line 10/)
    print(x)


main(True)
