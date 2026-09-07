# `copy_iter(...)` over a LIST LITERAL at a for-head: the literal leaves the
# element type unresolved, which the owning-rvalue capture cannot bind. The
# named-container source is pinned by tests/cases/list/copy_iter.
from tpy import copy_iter


def over_literal() -> None:
    for x in copy_iter([1, 2, 3]):  # tpyc: error(/for_each/)
        print(x)


def main() -> None:
    over_literal()


main()
