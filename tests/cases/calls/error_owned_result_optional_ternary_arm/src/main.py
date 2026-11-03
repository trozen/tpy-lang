# A getter that hands back a fresh record (`-> Own[Item]`) is no Optional
# ternary arm: the borrowed `Item | None` local would point at a temporary.
# The reference-returning getter's arm compiles (calls/getitem_call_parity);
# holding the fresh arm is BUGS.md#select-pointer-slot-gaps.
from tpy import int32, Own


class Item:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class VBag:
    def __init__(self) -> None:
        pass

    def __getitem__(self, i: int32) -> Own[Item]:
        return Item(i)


def pick(v: VBag, f: bool) -> None:
    p: Item | None = v.__getitem__(0) if f else None  # tpyc: error(/expr.ifexpr/)
    print(p is None)


pick(VBag(), True)
