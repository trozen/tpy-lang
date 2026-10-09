# A `__deref__` wrapper a PROPERTY builds fresh (`-> Own[R]`) is not supported
# at a record param, though the property is read off a named receiver: the
# wrapper is a temporary, and the borrow `__deref__()` returns dies with it.
# Bind the wrapper first: `r = h.fresh`, then `bump(r)`.
from tpy import int32, Own


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class R:
    _p: P

    def __init__(self) -> None:
        self._p = P(40)

    def __deref__(self) -> P:
        return self._p


class H:
    def __init__(self) -> None:
        pass

    @property
    def fresh(self) -> Own[R]:
        return R()


def bump(p: P) -> None:
    p.n += 1


def main() -> None:
    h = H()
    bump(h.fresh)  # tpyc: error(/record_f1/)


main()
