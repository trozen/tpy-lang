# A *args body view (varargs[T]) is a distinct type from Span[T] and cannot be
# passed where a Span[T] is expected -- a clean sema rejection, not a C++ error.
from tpy import Span


class Box:
    val: int

    def __init__(self, v: int) -> None:
        self.val = v


def consume(xs: Span[Box]) -> None:
    for b in xs:
        print(b.val)


def g(*items: Box) -> None:
    consume(items)  # tpyc: error(/expected Span\[Box\], got varargs\[Box\]/)


def main() -> None:
    g(Box(1), Box(2))


main()
