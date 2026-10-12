# A rebind in a loop whose own value keeps the element it replaces reuses the
# site's slot on the next pass: warned, and the carried element reads the new
# object (BUGS.md#loop-rebind-site-slot-reuse-warned). CPython prints
# `10 0`, `11 10`, `12 11`.
from tpy import Own, int32


class Box:
    def __init__(self, n: int32) -> None:
        self.n = n


def make_mixed(n: int32, b: Box) -> tuple[Own[Box], Box]:
    return (Box(n), b)


def loop_retain(v: Box) -> None:
    p = make_mixed(0, v)
    for i in range(3):
        # The rebind's own value keeps the old element 0 as its element 1.
        p = make_mixed(10 + i, p[0])  # tpyc: warning(/'p' will not keep the object it carries over from its previous value/)
        print("loop-retain", p[0].n, p[1].n)


loop_retain(Box(5))
