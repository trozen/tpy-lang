# A body-first reference-element tuple local read between its loop and a later
# head rejects with no read after (BUGS.md#for-head-rebind-of-reference-local-rejected).
from tpy import int32


class Node:
    def __init__(self, v: int32) -> None:
        self.v = v


def f(xs: list[tuple[Node, int32]]) -> None:
    for a in range(1, 3):
        p = (Node(a), a)
    print(p[0].v)
    # the read above declared the body's tuple local, so this head rebinds it
    for p in xs:  # tpyc: error(/for-loop rebind of reference-type variable 'p'/)
        p[0].v += 100


f([(Node(5), 1)])
