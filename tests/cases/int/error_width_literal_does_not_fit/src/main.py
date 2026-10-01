# A constructor first binding gives the local its type, so a literal stored
# later must fit that width. A documented divergence
# (docs/LANGUAGE_FEATURES.md "Numeric widening across reassignments"): CPython
# rebinds the name to the int.
from tpy import int8


def main() -> None:
    x = int8(3)
    x = 200  # tpyc: error(/Integer literal 200 is outside int8 range \[-128, 127\] in reassignment to 'x'/)
    print(x)


main()
