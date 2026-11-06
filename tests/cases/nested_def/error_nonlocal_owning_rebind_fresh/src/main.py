# Every binding of a `nonlocal` reference local from a nested def is refused,
# whatever binds it (BUGS.md#nonlocal-rebind-overwrites-in-place).
from tpy import int32


class Box:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def f(p: Box) -> None:
    a = Box(1)
    keep = a
    data = [11]
    d = {"a": 1}
    x = Box(3)
    y = Box(6)
    y = Box(7)

    def g() -> None:
        nonlocal a, data, d, x, y
        # `=` of a fresh object over a local that owns its object.
        a = Box(2)  # tpyc: error(/rebinding nonlocal 'a' is not supported yet: the write would replace the existing object rather than rebind the name/)
        # The walrus twin.
        print((a := Box(4)).n)  # tpyc: error(/rebinding nonlocal 'a' is not supported yet/)
        # A list and a dict.
        data = [12, 13]  # tpyc: error(/rebinding nonlocal 'data' is not supported yet/)
        d = {"b": 2}  # tpyc: error(/rebinding nonlocal 'd' is not supported yet/)
        # A `match` capture.
        match p:  # tpyc: error(/rebinding nonlocal 'x' is not supported yet/)
            case Box() as x:
                pass
        k = 0
        # A walrus in an augmented assignment's value, over an owning local
        # and over a rebound (pointer) local.
        k += (a := Box(8)).n  # tpyc: error(/rebinding nonlocal 'a' is not supported yet/)
        k += (y := Box(9)).n  # tpyc: error(/rebinding nonlocal 'y' is not supported yet/)
        print(k)

    g()
    a.n += 10
    print(keep.n, a.n, len(data), len(d), x.n, y.n)


f(Box(5))
