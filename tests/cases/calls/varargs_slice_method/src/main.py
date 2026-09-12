# Slice on a method's *args (not a free function). Routes through the same
# `list_slice(varargs<T, false>&, ...)` overload; verifies method dispatch
# doesn't disturb the resolution.
from tpy import int32


class Box:
    val: int32

    def __init__(self, v: int32) -> None:
        self.val = v


class Pile:
    def sum_tail(self, *items: Box) -> int32:  # tpyc: ok
        n: int32 = 0
        for b in items[1:]:
            n += b.val
        return n


def main() -> None:
    p = Pile()
    print(p.sum_tail(Box(10), Box(20), Box(30)))


main()
