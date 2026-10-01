# Sibling arms bind a local together, but a use in the first arm that needs
# the type on the spot (a list element) settles it at the literal's int32, so
# the later int64 arm is refused, naming that use. (A documented restriction:
# docs/LANGUAGE_FEATURES.md, "Numeric widening across reassignments".)
from tpy import int64


def main(c: bool) -> None:
    if c:
        x = 1
        ys = [x]
        print(ys)
    else:
        # the int64 arm after the first arm's use settled the local
        x = int64(5)  # tpyc: error(/'x' was used as int32 at line 11 \(a list element\), and this value is int64; annotate its first binding: x: int64 = 1/)
    print(x)


main(True)
