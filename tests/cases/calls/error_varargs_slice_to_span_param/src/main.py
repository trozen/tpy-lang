# A slice of a *args body view stays a varargs[T] (the distinction propagates
# through slicing), so it too is rejected where a Span[T] is expected.
from tpy import Span


class Box:
    val: int

    def __init__(self, v: int) -> None:
        self.val = v


def consume(xs: Span[Box]) -> None:
    for b in xs:
        print(b.val)


def g(*items: Box) -> None:
    consume(items[1:])  # tpyc: error(/expected Span\[Box\], got varargs\[Box\]/)


def main() -> None:
    g(Box(1), Box(2), Box(3))


main()
