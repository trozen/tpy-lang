# The `@nocopy` element of an `Own[tuple]` parameter passed to `Own[T]` while
# a name bound to it is still read: the alias is a borrower, so the element
# cannot move, and it has no copy to make.
from tpy import int32, Own, nocopy


@nocopy
class N:
    def __init__(self, n: int32) -> None:
        self.n = n


def sink(keep: list[N], b: Own[N]) -> int32:
    keep.append(b)
    return keep[-1].n


def live(p: Own[tuple[N, int32]]) -> int32:
    keep: list[N] = []
    o = p[0]
    r = sink(keep, p[0])  # tpyc: error(/@nocopy type 'N' cannot be passed as tuple element 0/)
    return r + o.n


def main() -> None:
    print(live((N(1), 2)))


main()
