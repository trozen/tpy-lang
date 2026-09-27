# A body-first tuple local with a reference element, read after a later `for`
# head over it, is rejected (BUGS.md#for-head-rebind-of-reference-local-rejected).
from tpy import int32


class Node:
    def __init__(self, v: int32) -> None:
        self.v = v


def f(xs: list[tuple[Node, int32]]) -> None:
    for a in range(1, 3):
        p = (Node(a), a)
    # the head rebinds the body's tuple local, whose first element is a Node
    for p in xs:  # tpyc: error(/for-loop rebind of reference-type variable 'p'/)
        pass
    p[0].v += 100
    print(p[0].v, p[1])


f([(Node(5), 1)])
