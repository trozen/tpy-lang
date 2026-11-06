# A nested def rebinding an enclosing pointer tuple through `nonlocal` is
# refused (BUGS.md#nonlocal-rebind-overwrites-in-place).
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
        # The closure rebinds the enclosing 'q', which owns its elements.
        q = make_mixed(5, v)  # tpyc: error(/rebinding nonlocal 'q' is not supported yet: the write would replace the existing object rather than rebind the name/)
    g()
    p = make_mixed(3, v)
    print(q[0].n, p[0].n)


f(Box(8))
