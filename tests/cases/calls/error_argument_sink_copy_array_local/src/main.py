# copy() of a literal-seeded list local kept as a fixed array is no list a
# mutated `list` parameter binds (BUGS.md#copy-array-local-at-mutated-list-param).
from tpy import copy, int32


class K:
    n: int32

    def __init__(self) -> None:
        self.n = 0

    def fill(self, xs: list[int32]) -> None:
        xs.append(3)
        self.n = len(xs)


def main() -> None:
    d = [7]
    k = K()
    k.fill(copy(d))  # tpyc: error(/method\.arg_shape/)
    print(k.n, len(d))


main()
