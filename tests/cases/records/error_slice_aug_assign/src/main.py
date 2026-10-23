# An augmented slice store through a record's slice `__getitem__` /
# `__setitem__` resolves both dunders in sema (a written slice key has no type
# of its own: the slice it builds) and is refused by code generation
# (BUGS.md#record-slice-assign-rejected).
from tpy import int32, basic_slice, Own


class R:
    xs: list[int32]

    def __init__(self) -> None:
        self.xs = [1, 2, 3]

    def __getitem__(self, k: basic_slice) -> Own[list[int32]]:
        return [self.xs[0], self.xs[1]]

    def __setitem__(self, k: basic_slice, v: Own[list[int32]]) -> None:
        self.xs = v


def main() -> None:
    r = R()
    r[0:2] += [9]  # tpyc: error(/stmt\.aug_assign/)
    print(r.xs)


main()
