# A @nocopy loop variable whose field a later-read local still borrows cannot
# move out of the loop (as the plain local in the same shape cannot) nor copy.
from tpy import int32, nocopy


class C:
    def __init__(self, v: int32) -> None:
        self.v = v


@nocopy
class P:
    n: int32
    cell: C

    def __init__(self, n: int32) -> None:
        self.n = n
        self.cell = C(n)


def main() -> None:
    ps = [P(1), P(2)]
    out: list[P] = []
    keep = C(5)
    for p in ps:
        keep = p.cell
        out.append(p)  # tpyc: error(/@nocopy type 'P' cannot be copied into 'value: Own\[P\]'/)
    keep.v += 10
    print(keep.v, len(out))


main()
