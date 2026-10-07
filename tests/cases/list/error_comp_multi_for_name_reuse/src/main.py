# Two `for` clauses of one comprehension binding the same name are refused:
# they share one scope, so the inner loop would rebind the outer variable.


def main() -> None:
    rows = [[1, 2], [3]]
    print([row for row in rows for row in row])  # tpyc: error(/.row. is bound by two .for. clauses of this comprehension; rename one/)


main()
