# Unpacking a MUTABLE Span[T] (non-value T) into a *args slot keeps working --
# control for the readonly-span rejection: only readonly sources are refused,
# a mutable span forwards its elements by reference like a list does.
from tpy import int32, Span, nocopy


@nocopy
class Box:
    val: int32

    def __init__(self, v: int32) -> None:
        self.val = v


def take_all(*items: Box) -> int32:
    n: int32 = 0
    for b in items:
        n += b.val
    return n


def use(xs: Span[Box]) -> int32:
    return take_all(*xs)


def main() -> None:
    items: list[Box] = []
    items.append(Box(1))
    items.append(Box(2))
    print(use(items))


main()
