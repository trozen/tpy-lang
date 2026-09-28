# A generator first bound in a block may not borrow a temporary tuple
# ELEMENT: the frame's tuple param borrows it exactly as a scalar param does
# (BUGS.md#generator-block-bind-borrows-local-rejects).
from typing import Iterator


class A:
    def __init__(self, v: int) -> None:
        self.v = v


def pair(p: tuple[A, A]) -> Iterator[int]:
    yield p[0].v
    yield p[1].v


def main(c: bool, a: A, b: A) -> None:
    if c:
        # `it` borrows the temporary A(7), which dies with the block.
        it = pair((A(7), b))
    else:
        it = pair((a, b))
    # The subject: a read after the block of a generator closed with it.
    print(list(it))  # tpyc: error(/cannot use 'it' after the 'if' block it is bound in: it borrows a temporary/)


main(True, A(1), A(2))
