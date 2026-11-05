# `iter(<rvalue>)` as a comprehension source rejects: the route has no row for
# it yet, although `::tpy::__iter__` owns a temporary of every family, so the
# iterator would keep it alive
# (BUGS.md#comp-iter-rvalue-source).
from tpy import int32, Own


def mk() -> Own[list[int32]]:
    return [1, 2, 3]


def main() -> None:
    # An `iter()` over a NAME stays admitted; only the rvalue argument rejects.
    print([v for v in iter(mk())])  # tpyc: error(/expr.list_comp/)


main()
