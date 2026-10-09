# A TEMPORARY `__deref__` wrapper at a record param is not supported: the
# borrow `__deref__()` returns lives in the wrapper, which the callee could
# outlive (a generator frame keeps its parameter). Bind the wrapper first.
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


def show(p: P) -> None:
    print(p.n)


def main() -> None:
    show(R())  # tpyc: error(/record_f1/)


main()
