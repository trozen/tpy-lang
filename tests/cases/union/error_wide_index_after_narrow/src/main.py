# A statically out-of-range subscript inside an isinstance-narrowed branch: the
# index is outside the subscript arm's range, so the body rejects. The
# isinstance narrow over a two-class union is pinned by
# tests/cases/union/union_isinstance_basic.
from tpy import Int32


class Leaf:
    n: Int32

    def __init__(self, n: Int32):
        self.n = n


class Inner:
    value: Int32

    def __init__(self, value: Int32):
        self.value = value


def wide_index(v: Leaf | Inner, xs: list[Int32]) -> Int32:
    if isinstance(v, Inner):
        return xs[9999999999]  # tpyc: error(/subscript\.index/)
    return v.n


def main() -> None:
    print(wide_index(Leaf(5), [1, 2]))


main()
