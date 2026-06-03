# Reading a reference (@nocopy) element through a Span[readonly[Box]] view --
# subscript and iteration -- compiles and borrows (no element copy; @nocopy
# would make a silent copy a compile error).
from tpy import Span, readonly, nocopy


@nocopy
class Box:
    val: int

    def __init__(self, v: int) -> None:
        self.val = v


def first(xs: Span[readonly[Box]]) -> int:
    return xs[0].val


def total(xs: Span[readonly[Box]]) -> int:
    t = 0
    for b in xs:
        t += b.val
    return t


def main() -> None:
    boxes: list[Box] = []
    boxes.append(Box(10))
    boxes.append(Box(20))
    print(first(boxes))
    print(total(boxes))


main()
