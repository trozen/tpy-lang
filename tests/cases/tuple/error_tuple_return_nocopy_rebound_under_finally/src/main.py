# A @nocopy member of a tuple returned under a finally whose `+=` resolves to
# `__add__` (a rebind that still reads the name first) keeps the eager
# capture, which would have to copy it: a located error, not a C++ failure.
# TO BE FIXED: CPython returns the original object; the planned fix gives the
# finally's rebind storage of its own so no copy is needed and this compiles
# (BUGS.md#finally-mutate-then-rebind-return).
from tpy import Own, int32, nocopy


@nocopy
class Tok:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __add__(self, k: int32) -> Own["Tok"]:
        return Tok(self.n + k)


def f() -> tuple[Own[Tok], int32]:
    t = Tok(1)
    try:
        return (t, 1)
    finally:
        t += 5  # tpyc: error(/'t' .*cannot be returned from inside a try while its finally rebinds it/)


def main() -> None:
    r, k = f()
    print(r.n, k)


main()
