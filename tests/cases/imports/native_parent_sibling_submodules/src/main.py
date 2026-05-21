# Two sibling non-native leaves under the same native parent package.
# Exercises the parent-walk's skip-and-mark path: the first import marks
# the native parent in `emitted_tpy_inits` (without emitting an init
# call), the second import sees the parent already marked and short-
# circuits without re-emission. Guards against the parent being added
# multiple times or being re-walked on the second sibling import.
from pkg.leaf_a import VAL_A
from pkg.leaf_b import VAL_B


def main() -> None:
    print(VAL_A)
    print(VAL_B)


main()
