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
        # The subject: `it` outlives the block the temporary A(7) dies with.
        it = pair((A(7), b))  # tpyc: error(/decl.frame_borrows_local/)
    else:
        it = pair((a, b))
    print(list(it))


main(True, A(1), A(2))
