# Concatenating an element read off a nested list literal still does not
# lower, even once the row is a list (BUGS.md#nested-literal-element-concat-rejects).
from tpy import int32


class Row:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def main() -> None:
    ll = [[Row(1)], [Row(2)]]
    ys = ll[0] + [Row(9)]  # tpyc: error(/binop\.shape/)
    print(len(ys))


main()
