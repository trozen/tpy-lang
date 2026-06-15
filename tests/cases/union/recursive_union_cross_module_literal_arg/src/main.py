# A non-generic recursive union (Shape) defined in another module, consumed via
# a cross-module function called with a container *literal* -- WITHOUT importing
# the Shape alias. Exercises cross-module recursive-union literal coercion
# (the general, non-json form). Read-only traversal.
from shapes import int_leaves


def main() -> None:
    print(int_leaves([1, "a", [2, 3], 4]))


main()
