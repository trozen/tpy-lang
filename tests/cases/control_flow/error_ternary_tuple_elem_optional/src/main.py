# The pointer-Optional sibling of the tuple-element ternary reject: taking the
# address of a borrow-form tuple's `std::get<N>` would yield `Rec**`, so the
# element-or-None select must keep rejecting.
from tpy import int32


class Rec:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def pick(ts: tuple[Rec, Rec], c: bool) -> int32:
    p = ts[0] if c else None  # tpyc: error(/expr\.ifexpr/)
    if p is not None:
        p.n += 10
        return p.n
    return -1


def main() -> None:
    print(pick((Rec(1), Rec(2)), True))


main()
