# An augmented store into a reference-type element (`m[0] += [5]`) is not
# lowered yet and says so (BUGS.md#aug-assign-reference-element-rejects).


def main() -> None:
    m = [[1], [2]]
    m[0] += [5]  # tpyc: error(/not yet supported by C\+\+ code generation \(stmt\.aug_assign\)/)
    print(m)


main()
