# `iter(<rvalue>)` as a comprehension source rejects: `::tpy::__iter__` has no
# owning overload, so the comp's `auto __obj_N = ::tpy::__iter__(mk());` capture
# would iterate a destroyed temporary. Every other admitted combinator owns its
# rvalue argument through a dedicated overload.
from tpy import int32, Own


def mk() -> Own[list[int32]]:
    return [1, 2, 3]


def main() -> None:
    # An `iter()` over a NAME stays admitted; only the rvalue argument rejects.
    print([v for v in iter(mk())])  # tpyc: error(/expr.list_comp/)


main()
