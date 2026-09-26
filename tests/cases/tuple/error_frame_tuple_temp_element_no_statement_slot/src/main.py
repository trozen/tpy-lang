# A temporary tuple ELEMENT handed to a generator frame at a position with no
# statement-level slot to hoist it into (an assert) rejects, like its scalar
# twin `one(A(3))` (BUGS.md#frame-temp-arg-no-statement-slot).
from typing import Iterator


class A:
    def __init__(self, v: int) -> None:
        self.v = v


def pair(p: tuple[A, A]) -> Iterator[int]:
    yield p[0].v
    yield p[1].v


def main() -> None:
    r = A(0)
    # The subject: A(3) would die before the frame is drained.
    assert sum(pair((A(3), r))) == 3  # tpyc: error(/btuple.frame_elem_no_statement_slot/)


main()
