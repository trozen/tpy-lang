# A temporary tuple ELEMENT handed to a generator frame at a position with no
# statement-level slot to hoist it into (a lambda body) rejects, like its scalar
# twin `one(A(q))` (BUGS.md#frame-temp-arg-no-statement-slot).
from typing import Callable, Iterator


class A:
    def __init__(self, v: int) -> None:
        self.v = v


def pair(p: tuple[A, A]) -> Iterator[int]:
    yield p[0].v
    yield p[1].v


def ap(f: Callable[[int], int]) -> int:
    return f(3)


def main() -> None:
    # The subject: A(q) would die before the frame is drained.
    print(ap(lambda q: sum(pair((A(q), A(0))))))  # tpyc: error(/btuple.frame_elem_no_statement_slot/)


main()
