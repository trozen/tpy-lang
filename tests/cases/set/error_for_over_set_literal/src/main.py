# A set literal directly as a for-loop iterable: only the list-literal shape has
# an owning-temporary arm, so the set spelling rejects. The list-literal
# iterable is pinned by tests/cases/stdlib/heapq.
from tpy import int32


def from_set() -> int32:
    t = 0
    for x in {2, 4}:  # tpyc: error(/iter\.set_literal_shape/)
        t = t + x
    return t


def main() -> None:
    print(from_set())


main()
