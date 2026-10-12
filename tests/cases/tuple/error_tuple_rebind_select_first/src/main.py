# The first binding of a reassigned alias of rebound tuples, a conditional
# expression over them, is refused in words as a later one is
# (error_tuple_rebind_select_source); an if statement binding each arm
# compiles (BUGS.md#ptr-tuple-capture-and-binder-rejects).
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
    # The alias's first binding chooses between the two tuples.
    q = p if c else s  # tpyc: error(/binding 'q', which holds the elements of the tuples it is bound to, to a conditional expression is not yet supported here; bind 'q' in an if statement instead/)
    q[0].n += 100
    q = p
    print(p[0].n, q[0].n, s[0].n)


f(Box(3), True)
