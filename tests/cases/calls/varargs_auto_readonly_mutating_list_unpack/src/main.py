# *list unpack into a mutating vararg slot: the Phase-2 mutation edge from
# the unpacked param root to the callee's vararg slot must propagate
# `bump_all` mutating back to `xs`, so `xs` stays `std::vector<Box>&`
# (non-const). Replaces the removed Phase-1 caller-side marking with the
# call-edge mechanism.
from tpy import int32


class Box:
    val: int32

    def __init__(self, v: int32) -> None:
        self.val = v


def bump_all(*items: Box) -> None:
    for b in items:
        b.val += 1


def via_unpack(xs: list[Box]) -> None:  # tpyc: ok
    bump_all(*xs)


def main() -> None:
    items: list[Box] = []
    items.append(Box(5))
    items.append(Box(6))
    via_unpack(items)
    print(items[0].val)
    print(items[1].val)


main()
