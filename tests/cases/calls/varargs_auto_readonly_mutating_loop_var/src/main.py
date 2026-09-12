# Mutating-vararg + loop-var individual arg: `bump_all` mutates, so its slot
# stays `varargs<Box>` (mutable). The for-loop var `b` is passed as an
# individual arg and address-taken into the indirect-mode `Box*` pack -- so
# the loop binding must stay `auto& b` (not `const auto& b`) or `&b` would
# yield `const Box*` and mismatch the slot.
from tpy import int32


class Box:
    val: int32

    def __init__(self, v: int32) -> None:
        self.val = v


def bump_all(*items: Box) -> None:
    for b in items:
        b.val += 1


def via_loop(xs: list[Box]) -> None:  # tpyc: ok
    for b in xs:
        bump_all(b)


def main() -> None:
    items: list[Box] = []
    items.append(Box(5))
    items.append(Box(6))
    via_loop(items)
    print(items[0].val)
    print(items[1].val)


main()
