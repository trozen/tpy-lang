# A readonly getter's result, aliased and returned, still borrows the
# getter's receiver, so returning it off a local is the dangling-return
# error (docs/LANGUAGE_FEATURES.md "Owned Return Values").
from tpy import int32, readonly


class Cell:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class P:
    cell: Cell

    def __init__(self) -> None:
        self.cell = Cell(1)

    @property
    def ro(self) -> readonly[Cell]:
        return self.cell


class Outer:
    inner: P

    def __init__(self) -> None:
        self.inner = P()

    # method: the same alias returned from a method borrows the receiver.
    def m(self) -> readonly[Cell]:
        q = self.inner.ro
        return q


def aliased(p: Outer) -> readonly[Cell]:
    q = p.inner.ro  # the alias still borrows `p`, through the getter
    return q  # so the result borrows `p`


# The method version off a temporary receiver warns; it sits here, not in a
# running case, because the C++ reference it binds really dangles.
def method_warns() -> int32:
    r = Outer().m()  # tpyc: warning(/Result borrows from temporary receiver/)
    return r.n


def escape() -> readonly[Cell]:
    p = Outer()
    return aliased(p)  # tpyc: error(/Cannot return local or temporary as reference/)


def main() -> None:
    print(escape().n)


main()
