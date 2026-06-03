# A readonly[Box] element of a Span[readonly[Box]] cannot be passed to a
# function expecting a mutable Box -- rejected at sema, not by C++ const.
from tpy import Span, readonly


class Box:
    val: int

    def __init__(self, v: int) -> None:
        self.val = v


def takes_mut(b: Box) -> None:
    b.val = 1


def f(xs: Span[readonly[Box]]) -> None:
    takes_mut(xs[0])  # tpyc: error(/Cannot pass readonly\[Box\] as mutable Box/)


def main() -> None:
    pass


main()
