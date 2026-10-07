# `_` bound by two `for` clauses is refused once the comprehension reads it:
# the inner clause rebinds the variable the filter reads (docs/LANGUAGE_FEATURES.md,
# comprehension rules); CPython runs it.


def main() -> None:
    a = [5]
    b = [9]
    # the filter reads `_`, which the last clause rebinds
    print([x for _ in a for x in range(3) if _ == 5 for _ in b])  # tpyc: error(/._. is bound by two .for. clauses of this comprehension; rename one/)


main()
