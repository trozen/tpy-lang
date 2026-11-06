# A walrus element in a borrow-form tuple literal is refused: its write would
# sit beside the other elements' reads (BUGS.md#subexpression-right-to-left-eval).
from tpy import int32


class A:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def pair(t: tuple[A, A]) -> None:
    print(t[0].n, t[1].n)


def f() -> None:
    b = A(1)
    # The walrus beside a read of the name it rebinds is the subject.
    pair((b, (b := A(2))))  # tpyc: error(/this construct is not yet supported by C\+\+ code generation/)


f()
