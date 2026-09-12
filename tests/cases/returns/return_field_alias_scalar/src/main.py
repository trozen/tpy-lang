# Returning a scalar reference through a local alias of a param's field
# (b = h.box; return b) marks the param mutated through the field-path
# borrow chain, so it stays non-const and the caller's mutation through
# the returned reference reaches the field -- same as the direct
# `return h.box` form.
from tpy import int32


class Box:
    val: int32
    def __init__(self, v: int32) -> None:
        self.val = v


class Holder:
    box: Box
    def __init__(self, b: Box) -> None:
        self.box = b


def ret(h: Holder) -> Box:
    b = h.box
    return b


def main() -> None:
    h = Holder(Box(5))
    b = ret(h)
    b.val = 99
    print(h.box.val)


main()
