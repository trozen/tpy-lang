# A plain record is always truthy, but reaching `a.b.c` must still check that
# `a.b` is not None -- the always-true fold used to drop the whole render and
# with it the check, so a None `b` silently took the branch. CPython raises
# AttributeError here; TPy panics on the null-optional deref.
class C:
    v: int

    def __init__(self, v: int):
        self.v = v


class B:
    c: C

    def __init__(self, c: C):
        self.c = c


class A:
    b: B | None

    def __init__(self, b: B | None):
        self.b = b


def probe(a: A) -> int:
    if a.b.c:  # tpyc: warning(/Potential None access/)
        return 1
    return 0


def main() -> None:
    present = B(C(1))
    print("present:", probe(A(present)))
    print("none:", probe(A(None)))


main()
