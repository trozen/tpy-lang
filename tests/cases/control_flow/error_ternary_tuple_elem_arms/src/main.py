# A TUPLE element is not a container element: a borrow-form tuple's
# `std::get<N>` yields the element pointer, not the referent, so an lvalue
# ternary over two tuple elements would bind a reference to a pointer.
from tpy import int32


class Rec:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def pick(ts: tuple[Rec, Rec], c: bool) -> int32:
    r = ts[0] if c else ts[1]  # tpyc: error(/expr\.ifexpr/)
    r.n += 10
    return r.n


def main() -> None:
    print(pick((Rec(1), Rec(2)), True))


main()
