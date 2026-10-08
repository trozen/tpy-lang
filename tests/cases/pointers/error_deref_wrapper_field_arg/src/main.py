# A `__deref__` wrapper read off a FIELD at a record param keeps rejecting:
# the wrapper flavor passes a copy of the target, so a write through the param
# would miss the wrapped object (BUGS.md#deref-wrapper-arg-mutates-copy).
from tpy import int32


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class R:
    _p: P

    def __init__(self) -> None:
        self._p = P(5)

    def __deref__(self) -> P:
        return self._p


class H:
    r: R

    def __init__(self) -> None:
        self.r = R()


def show(p: P) -> None:
    print(p.n)


def main() -> None:
    h = H()
    show(h.r)  # tpyc: error(/record_f1/)


main()
