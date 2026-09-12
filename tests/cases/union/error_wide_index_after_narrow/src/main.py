# A statically out-of-range subscript inside an isinstance-narrowed branch: the
# index is outside the subscript arm's range, so the body rejects. The
# isinstance narrow over a two-class union is pinned by
# tests/cases/union/union_isinstance_basic.
from tpy import int32


class Leaf:
    n: int32

    def __init__(self, n: int32):
        self.n = n


class Inner:
    value: int32

    def __init__(self, value: int32):
        self.value = value


def wide_index(v: Leaf | Inner, xs: list[int32]) -> int32:
    if isinstance(v, Inner):
        return xs[9999999999]  # tpyc: error(/subscript\.index/)
    return v.n


def main() -> None:
    print(wide_index(Leaf(5), [1, 2]))


main()
