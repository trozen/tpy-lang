# A record ternary mixing an ELEMENT lvalue with a ctor prvalue must keep
# rejecting: the C++ `?:` over a mixed value-category pair is a prvalue, which
# silently COPIES the lvalue arm (BUGS.md#ternary-receiver-mixed-category-copies).
# Only an all-lvalue or all-prvalue arm pair is lowered.
from tpy import int32


class Rec:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def f(rs: list[Rec], c: bool) -> int32:
    r = rs[0] if c else Rec(9)  # tpyc: error(/decl\.slot_type/)
    r.n += 1
    return r.n


def main() -> None:
    print(f([Rec(1)], True))


main()
