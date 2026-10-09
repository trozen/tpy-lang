# A rebind of a mixed tuple local while a name still refers to the object its
# owned element was bound to: the new element's object needs storage of its
# own (each owned element is a scalar's object), which a function body does
# not have yet: refused at the rebind, naming the alias
# (BUGS.md#tuple-rebind-clobbers-live-alias), where writing in place would
# overwrite the object 'a' still refers to.
from tpy import Own, int32


class Box:
    def __init__(self, n: int32) -> None:
        self.n = n


def make_mixed(n: int32, b: Box) -> tuple[Own[Box], Box]:
    return (Box(n), b)


def rebind(v: Box, w: Box) -> None:
    p = make_mixed(1, v)
    a = p[0]
    p = make_mixed(9, w)  # tpyc: error(/'a' still refers to the object an element of 'p' was bound to, so the object the rebind of 'p' creates needs storage of its own/)
    a.n += 100
    print(a.n, p[0].n)


rebind(Box(10), Box(20))
