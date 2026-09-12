# Container subscript READS of scalar elements -- list[int32] (literal and dynamic
# index) and dict[int32, int32] (fixed-int key). These are the shapes the THIR
# container-subscript-read cell routes to `::tpy::__getitem__(c, i)`. The results are
# value scalars (copied on both paths), so there is no reference/aliasing distinction
# to force here. The whole-corpus --thir-codegen byte-diff exercises the routed emit.
from tpy import int32


def first(items: list[int32]) -> int32:
    return items[0]


def at(items: list[int32], i: int32) -> int32:
    return items[i]


def sum_two(items: list[int32], i: int32, j: int32) -> int32:
    return items[i] + items[j]


def dget(d: dict[int32, int32], k: int32) -> int32:
    return d[k]


class Box:
    xs: list[int32]

    def __init__(self) -> None:
        self.xs = [1, 2, 3]

    def get(self) -> list[int32]:
        return self.xs


def read_through_call(b: Box) -> int32:
    return b.get()[0]  # tpyc: ok -- the receiver is a CALL, not a name


def main() -> None:
    xs = [10, 20, 30]
    print(first(xs))
    print(at(xs, 2))
    print(sum_two(xs, 0, 1))
    scores = {1: 100, 2: 200}
    print(dget(scores, 2))
    print(read_through_call(Box()))


main()
