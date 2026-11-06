# A nested def re-pointing an enclosing tuple at another tuple's elements
# through `nonlocal` is refused (BUGS.md#nonlocal-reference-rebind-copies).
from tpy import Own, int32


class Box:
    def __init__(self, n: int32) -> None:
        self.n = n


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
        q = p  # tpyc: error(/rebinding nonlocal 'q' is not supported yet: the write would replace the existing object rather than rebind the name/)
    g()
    p = make_mixed(3, v)
    print(q[0].n, p[0].n)


f(Box(8))
