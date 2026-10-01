# A float literal stored into a float32 local must round to a finite float32,
# as an int literal must fit the fixed width it is stored into; the message
# keeps the literal's sign. A documented divergence (docs/LANGUAGE_FEATURES.md
# "Numeric widening across reassignments"): CPython stores the double.
from tpy import float32


def main() -> None:
    f = float32(1.0)
    f = -1e39  # tpyc: error(/Float literal -1e\+39 is outside float32 range \[-3\.4028235e\+38, 3\.4028235e\+38\]/)
    print(f)


main()
