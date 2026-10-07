# An owned `@nocopy` tuple element passed to `Own[T]` while the tuple is still
# read afterwards: it cannot move (the tuple is live) and has no copy to make.
from tpy import int32, Own, nocopy


@nocopy
class N:
    def __init__(self, n: int32) -> None:
        self.n = n


def sink(keep: list[N], b: Own[N]) -> int32:
    keep.append(b)
    return keep[-1].n


def live(p: tuple[Own[N], int32]) -> int32:
    keep: list[N] = []
    r = sink(keep, p[0])  # tpyc: error(/@nocopy type 'N' cannot be passed as tuple element 0/)
    return r + p[1]


def main() -> None:
    print(live((N(1), 2)))


main()
