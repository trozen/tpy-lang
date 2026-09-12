# Generic vararg `*items: T` with non-mutating body: the slot flips to
# `varargs<const T>` at the template, and per-instantiation T comes from the
# call site (no T-side const propagation needed -- the template-level slot
# const is enough).
from tpy import int32


class Box:
    val: int32

    def __init__(self, v: int32) -> None:
        self.val = v


def count_them[T](*items: T) -> int32:  # tpyc: ok
    return len(items)


def via_param(b: Box, c: Box) -> int32:
    return count_them(b, c)


def main() -> None:
    x = Box(7)
    y = Box(8)
    print(via_param(x, y))


main()
