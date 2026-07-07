# Record-rvalue args at ctor call sites: a const slot binds the prvalue
# inline; a slot the ctor mutates hoists a named temp; const chains nest.
# The rvalues are anonymous temporaries, so no aliasing is observable and
# plain value output pins both paths.
from tpy import Int32, Own


class Inner:
    v: Int32

    def __init__(self, v: Int32):
        self.v = v

    def bump(self) -> None:
        self.v += 1


class HolderConst:
    x: Int32

    def __init__(self, inner: Inner):
        self.x = inner.v


class HolderMut:
    x: Int32

    def __init__(self, inner: Inner):
        inner.bump()
        self.x = inner.v


class Outer:
    y: Int32

    def __init__(self, h: HolderConst):
        self.y = h.x


def make_inner(v: Int32) -> Own[Inner]:
    return Inner(v)


def main() -> None:
    a = HolderConst(Inner(1))
    b = HolderMut(Inner(2))
    c = HolderConst(make_inner(3))
    d = HolderMut(make_inner(4))
    e = Outer(HolderConst(Inner(5)))
    print(a.x, b.x, c.x, d.x, e.y)


main()
