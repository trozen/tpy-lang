# `key in dict` and `key in set` with a tuple-typed key: operator dispatch
# must reach the container's __contains__ rather than treat the LHS tuple
# as a tuple operation. Also exercises tuple-literal membership
# (`x in (1, 2, 3)`) and dict/set/list literals with tuple-of-literal
# keys/elements (the literal-resolution recurses into tuples).
from tpy import int32


def main() -> None:
    # Annotated dict/set literals with tuple-of-literals keys/elements.
    d: dict[tuple[int32, int32], str] = {(1, 2): "a", (3, 4): "b"}
    s: set[tuple[int32, int32]] = {(1, 2), (5, 6)}
    # Unannotated dict literal: exercises the default-int resolution path.
    d2 = {(1, 2): "a", (3, 4): "b"}
    # List literal of tuples mixing literal and concrete element types.
    pairs = [(1, 2), (int32(3), 4)]

    k1: tuple[int32, int32] = (1, 2)
    k2: tuple[int32, int32] = (9, 9)

    print(k1 in d, k2 in d)
    print(k1 not in d, k2 not in d)
    print(k1 in s, k2 in s)
    # Tuple-literal LHS (`(a, b) in d`) -- IntLiteralType elements coerce
    # to the dict's concrete key type at the membership-test boundary.
    print((1, 2) in d, (9, 9) in d)
    print(d2[(1, 2)])
    print(pairs[0][0], pairs[1][0])

    # Tuple-literal membership still works (regression guard).
    x: int32 = 2
    print(x in (1, 2, 3), x in (4, 5, 6))


main()
