# A tuple name's union leaf with a @nocopy alternative copied into a list
# literal is an error: the union has no copy to make.
from tpy import int32, nocopy


class A:
    def __init__(self, v: int32) -> None:
        self.v = v


@nocopy
class N:
    def __init__(self, v: int32) -> None:
        self.v = v


def keep(t: tuple[A | N, int32]) -> None:
    xs: list[tuple[A | N, int32]] = [t]  # tpyc: error(/cannot copy non-copyable type 'A \| N' into owned storage \(tuple element 0\)/)
    print(len(xs))


def main() -> None:
    keep((A(1), 2))


main()
