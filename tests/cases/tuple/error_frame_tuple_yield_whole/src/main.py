# A generator yielding its rebound owning tuple whole is refused: the consumer
# would hold pointers into the frame's element storage, which the next rebind
# may write in place (BUGS.md#genexpr-yields-captured-reference-local).
from typing import Iterator

from tpy import Own, int32


class Box:
    def __init__(self, n: int32) -> None:
        self.n = n


def make_mixed(n: int32, b: Box) -> tuple[Own[Box], Box]:
    return (Box(n), b)


# the reject is located at the frame's def, not at the yield inside it
def gen(v: Box) -> Iterator[tuple[Box, Box]]:  # tpyc: error(/res\.btuple_yield_source/)
    p = make_mixed(1, v)
    # the subject: `p` itself is handed out
    yield p
    p = make_mixed(2, v)
    yield p


def main() -> None:
    g = gen(Box(5))
    for t in g:
        a = t[0]
        for u in g:
            print("held", a.n, u[0].n)


main()
