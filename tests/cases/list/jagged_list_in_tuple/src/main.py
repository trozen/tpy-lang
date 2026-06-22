# A jagged pending list wrapped in a tuple, where the tuples are sibling
# elements of a homogeneous list: the inner variable-length lists must converge
# to list[int] (vector) across siblings, not just when the list is a bare
# element. Mutating the inner list through the tuple must be observed.
def main() -> None:
    xs = [(1, [2, 3]), (4, [5, 6, 7])]  # tpyc: ok
    print(xs)
    xs[1][1].append(9)  # inner list is a real vector reached through the tuple
    print(xs)

main()
