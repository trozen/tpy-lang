# A literal at a pointer-tuple rebind whose element is an element of another
# tuple (here one that aliases the rebound tuple) is refused with the rewrite
# that compiles (BUGS.md#ptr-tuple-literal-element-rejects).
from tpy import Own, int32


class Box:
    def __init__(self, n: int32) -> None:
        self.n = n


def pair(n: int32) -> Own[tuple[Box, Box]]:
    return (Box(n), Box(n + 1))


def keep() -> None:
    p = pair(1)
    q = p
    p = (Box(9), q[1])  # tpyc: error(/element 1 of the tuple bound to 'p' here is an element of the tuple 'q'.*bind it to a name of its own first/)
    q[1].n += 100
    print(p[0].n, p[1].n, q[0].n)


keep()
