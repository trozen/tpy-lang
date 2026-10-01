# A bare literal beside a uint32 arm is refused as beside an int8 one: the
# default int32 has no common type with uint32, so the arm read first would
# decide the local's type.
# (A documented restriction: docs/LANGUAGE_FEATURES.md, "Numeric widening
# across reassignments".)
from tpy import uint32


def main(c: bool) -> None:
    if c:
        x = uint32(3)
    else:
        # the literal arm beside the uint32 one
        x = 0  # tpyc: error(/'x' is uint32 in one arm \(line 11\) and a bare literal in the other \(line 14\), so its type depends on which arm is read first; write uint32\(0\), or annotate x: uint32 at line 11/)
    print(x)


main(True)
