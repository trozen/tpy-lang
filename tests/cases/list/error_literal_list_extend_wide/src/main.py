# `extend` decides an unannotated list literal's element; a literal it adds that
# the element does not hold is refused: BUGS.md#literal-list-extend-wide-literal-refused.
def main() -> None:
    xs = [1]
    n = xs[0]
    # extend read the list as int32 elements; the literal needs more.
    xs.extend([1099511627776])  # tpyc: error(/literal 1099511627776 is outside int32 range/)
    print(xs, n)


main()
