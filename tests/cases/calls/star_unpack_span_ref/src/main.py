# Unpacking a MUTABLE Span[T] (non-value T) into a *args slot keeps working --
# control for the readonly-span rejection: only readonly sources are refused,
# a mutable span forwards its elements by reference like a list does.
from tpy import Int32, Span, nocopy


@nocopy
class Box:
    val: Int32

    def __init__(self, v: Int32) -> None:
        self.val = v


def take_all(*items: Box) -> Int32:
    n: Int32 = 0
    for b in items:
        n += b.val
    return n


def use(xs: Span[Box]) -> Int32:
    return take_all(*xs)


def main() -> None:
    items: list[Box] = []
    items.append(Box(1))
    items.append(Box(2))
    print(use(items))


main()
