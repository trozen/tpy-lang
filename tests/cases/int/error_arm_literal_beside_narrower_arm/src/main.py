# Sibling arms bind a local together: a bare literal beside an int8 arm is
# refused, since the literal arm read first would make the local int32 and the
# int8 arm read first would make it int8.
# A literal arm beside a float32 arm is refused the same way (unit rows in
# tpyc/sema/test_pending_num.py).
# (A documented restriction: docs/LANGUAGE_FEATURES.md, "Numeric widening
# across reassignments".)
from tpy import int8


def main(c: bool) -> None:
    if c:
        # the literal arm beside the int8 one
        x = 1  # tpyc: error(/'x' is int8 in one arm \(line 16\) and a bare literal in the other \(line 14\), so its type depends on which arm is read first; write int8\(1\), or annotate x: int8 at line 14/)
    else:
        x = int8(3)
    print(x)


main(True)
