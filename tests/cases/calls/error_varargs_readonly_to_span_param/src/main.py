# A readonly *args body view (varargs[readonly[Box]]) is still a varargs, not a
# Span, so passing it to a Span[readonly[Box]] parameter is rejected at sema.
from tpy import Span, readonly


class Box:
    val: int

    def __init__(self, v: int) -> None:
        self.val = v


def consume(xs: Span[readonly[Box]]) -> None:
    for b in xs:
        print(b.val)


def g(*items: readonly[Box]) -> None:
    consume(items)  # tpyc: error(/expected Span\[readonly\[Box\]\], got varargs\[readonly\[Box\]\]/)


def main() -> None:
    g(Box(1), Box(2))


main()
