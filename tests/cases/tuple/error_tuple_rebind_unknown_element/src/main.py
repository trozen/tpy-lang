# A literal at a pointer-tuple rebind with an element whose storage no route
# models (a ternary of names) is refused at the element
# (BUGS.md#ptr-tuple-literal-element-rejects).
from tpy import Own, int32


class Box:
    def __init__(self, n: int32) -> None:
        self.n = n


def make_mixed(n: int32, b: Box) -> tuple[Own[Box], Box]:
    return (Box(n), b)


def f(c: bool, w: Box, v: Box) -> None:
    p = make_mixed(1, v)
    # Element 1 is a select between two objects held elsewhere.
    p = (Box(9), w if c else v)  # tpyc: error(/element 1 of the tuple bound to 'p' here is an expression whose storage is not known here.*bind it to a name of its own first/)
    p[1].n += 100
    print(p[1].n, w.n)


f(True, Box(4), Box(1))
