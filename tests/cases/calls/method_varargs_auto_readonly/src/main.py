# Non-mutating method *xs: Box: the sync pass walks record methods too, so
# the slot flips to `varargs<const Box>` and the method picks up the implicit
# `const` overload via the auto-readonly self-mutation inference (independent
# but composes naturally).
from tpy import int32


class Box:
    val: int32

    def __init__(self, v: int32) -> None:
        self.val = v


class Pile:
    def total(self, *boxes: Box) -> int32:  # tpyc: ok
        s: int32 = 0
        for b in boxes:
            s += b.val
        return s


def main() -> None:
    p = Pile()
    a = Box(1)
    b = Box(2)
    c = Box(3)
    print(p.total(a, b, c))


main()
