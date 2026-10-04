# `iter(<rvalue>)` as a comprehension source rejects: `::tpy::__iter__` owns a
# temporary list or array but not every family (a set, a dict), so the route
# refuses every rvalue source until they all own theirs
# (BUGS.md#comp-iter-rvalue-source).
from tpy import int32, Own


def mk() -> Own[list[int32]]:
    return [1, 2, 3]


def main() -> None:
    # An `iter()` over a NAME stays admitted; only the rvalue argument rejects.
    print([v for v in iter(mk())])  # tpyc: error(/expr.list_comp/)


main()
