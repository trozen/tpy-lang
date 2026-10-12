# A reference into other storage (`xs[0]`) at a position a pointer-tuple
# rebind owns has no binding route yet, so it is refused at the element; the
# remedy, a name of its own, takes the warned copy an owned position makes
# (BUGS.md#ptr-tuple-literal-element-rejects).
from tpy import Own, int32


class Box:
    def __init__(self, n: int32) -> None:
        self.n = n


def make_mixed(n: int32, b: Box) -> tuple[Own[Box], Box]:
    return (Box(n), b)


def f(xs: list[Box], v: Box) -> None:
    p = make_mixed(1, v)
    # Element 0 is a position the binding owns.
    p = (xs[0], v)  # tpyc: warning(/copies Box into owned storage \(tuple element 0\)/) error(/element 0 of the tuple bound to 'p' here is a reference into storage held elsewhere.*bind it to a name of its own first/)
    p[0].n += 100
    print(p[0].n, xs[0].n)


f([Box(4)], Box(1))
