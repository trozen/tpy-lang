# A literal at a pointer-tuple rebind whose element is an element of the
# tuple itself points into the tuple's own storage, a local: refused like any
# binding owning nothing whose root is no parameter
# (BUGS.md#ptr-tuple-repoint-root-rejects,
# BUGS.md#ptr-tuple-literal-element-rejects).
from tpy import int32


class Box:
    def __init__(self, n: int32) -> None:
        self.n = n


def swap() -> None:
    p = (Box(1), Box(2))
    p = (p[1], p[0])  # tpyc: error(/'p' keeps objects of its own in storage of 'swap', so a binding of it that owns nothing may point only into a parameter's or self's storage for now; here it points into 'p' itself; bind this value to a name of its own/)
    p[1].n += 100
    print(p[0].n, p[1].n)


swap()
