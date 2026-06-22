# Deep-nested jagged list literal: an internally-jagged middle list
# ([[5, 6], [7, 8, 9]]) shares a same-size peer ([[1, 2], [3, 4]]). The jagged
# innermost level must resolve to list[int] (vector) and propagate to its
# equal-size sibling, while the uniform outer/middle levels stay fixed Array.
# Mutating the innermost list after construction must be observed (the element
# is a real vector stored in the array, not a copy).
def main() -> None:
    xs = [[[1, 2], [3, 4]], [[5, 6], [7, 8, 9]]]  # tpyc: ok
    print(xs)
    xs[1][1].append(99)  # innermost is a real vector -> grows; change observed
    print(xs)
    xs[0][0].append(77)  # the equal-size sibling demoted too, so it grows as well
    print(xs)

main()
