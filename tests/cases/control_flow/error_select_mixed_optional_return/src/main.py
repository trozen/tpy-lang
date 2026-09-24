# A select of a name and a fresh object at a pointer-repr `T | None` return:
# the fresh object's select slot would die before the caller reads the
# pointer, so the return rejects.
from tpy import int32, Own


class C:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def make() -> Own[C]:
    return C(10)


def pick(c: bool, a: C) -> C | None:
    return a if c else make()  # tpyc: error(/would dangle/)


def main() -> None:
    a = C(1)
    r = pick(True, a)
    if r is not None:
        print(r.n)


main()
