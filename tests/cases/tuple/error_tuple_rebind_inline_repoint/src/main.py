# Re-pointing a rebound pointer tuple at a tuple that holds an element inline:
# nothing proves the source outlives the name, so it is refused in words --
# the source is a local, a root no binding owning nothing may point into
# (BUGS.md#ptr-tuple-repoint-root-rejects).
from tpy import Own, int32


class Box:
    def __init__(self, n: int32) -> None:
        self.n = n


def make_mixed(n: int32, b: Box) -> tuple[Own[Box], Box]:
    return (Box(n), b)


def repoint(c: bool, v: Box) -> None:
    p = make_mixed(1, v)
    p = make_mixed(2, v)
    if c:
        q = (Box(7), v)
        p = q  # tpyc: error(/'p' keeps objects of its own in storage of 'repoint', so a binding of it that owns nothing may point only into a parameter's or self's storage for now; here it points into 'q', a local of 'repoint'; bind this value to a name of its own/)
    p[0].n += 100
    print(p[0].n)


repoint(True, Box(5))
