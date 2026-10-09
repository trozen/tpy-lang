# A `__deref__` wrapper handed back by a method of a TEMPORARY receiver is not
# supported at a record param: the generator frame would keep a reference
# into the receiver, which dies at the end of the statement. Bind the wrapper
# first: `h = H()`, `r = h.get_r()`, then `gen_frame(r)`.
from typing import Iterator
from tpy import int32


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class R:
    _p: P

    def __init__(self) -> None:
        self._p = P(0)

    def __deref__(self) -> P:
        return self._p


class H:
    r: R

    def __init__(self) -> None:
        self.r = R()

    def get_r(self) -> R:
        return self.r


def gen_frame(p: P) -> Iterator[int32]:
    p.n += 1
    yield p.n


def main() -> None:
    for v in gen_frame(H().get_r()):  # tpyc: error(/record_f1/)
        print(v)


main()
