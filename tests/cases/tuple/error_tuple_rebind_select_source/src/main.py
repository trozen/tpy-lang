# A reassigned alias of rebound tuples bound to a conditional expression over
# them has no binding route yet, so it is refused in words; an if statement
# binding each arm compiles (BUGS.md#ptr-tuple-capture-and-binder-rejects).
from tpy import Own, int32


class Box:
    def __init__(self, n: int32) -> None:
        self.n = n


def make_mixed(n: int32, b: Box) -> tuple[Own[Box], Box]:
    return (Box(n), b)


def f(v: Box, c: bool) -> None:
    p = make_mixed(1, v)
    p = make_mixed(2, v)
    s = make_mixed(3, v)
    s = make_mixed(4, v)
    q = p
    # The alias re-pointed at a choice between the two tuples.
    q = p if c else s  # tpyc: error(/binding 'q', which holds the elements of the tuples it is bound to, to a conditional expression is not yet supported here; bind 'q' in an if statement instead/)
    q[0].n += 100
    print(p[0].n, q[0].n, s[0].n)


f(Box(3), True)
