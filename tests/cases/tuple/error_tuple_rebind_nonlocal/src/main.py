# A nested def re-pointing an enclosing rebound tuple, or an alias of one,
# through `nonlocal` would be invisible to the enclosing body's in-place
# rebinds, so it is refused at the closure's binding
# (BUGS.md#nonlocal-rebind-overwrites-in-place).
from tpy import Own, int32


class Box:
    def __init__(self, n: int32) -> None:
        self.n = n


class H:
    pair: tuple[Box, Box]

    def __init__(self) -> None:
        self.pair = (Box(5), Box(6))


def make_mixed(n: int32, b: Box) -> tuple[Own[Box], Box]:
    return (Box(n), b)


def f(v: Box) -> None:
    p = make_mixed(1, v)
    p = make_mixed(2, v)
    q = make_mixed(0, v)
    q = make_mixed(0, v)

    def g() -> None:
        nonlocal q
        # The closure re-points the enclosing 'q' at 'p''s elements.
        q = p  # tpyc: error(/'q' is rebound here through 'nonlocal', and it keeps objects of its own in storage of 'f' itself; rebinding it from a nested function is not yet supported here; rebind it in 'f' itself/)
    g()
    p = make_mixed(3, v)
    print(q[0].n, p[0].n)


def f2(h: H, v: Box) -> None:
    p = make_mixed(1, v)
    p = make_mixed(2, v)
    q = p

    def g() -> None:
        nonlocal q
        # The closure re-points the enclosing alias of 'p' at a field.
        q = h.pair  # tpyc: error(/'q' is rebound here through 'nonlocal', and it may hold the objects 'p' keeps in storage of 'f2' itself; rebinding it from a nested function is not yet supported here; rebind it in 'f2' itself/)
    g()
    q[0].n += 100
    p = make_mixed(3, v)
    print(p[0].n, q[0].n, h.pair[0].n)


f(Box(8))
f2(H(), Box(3))
