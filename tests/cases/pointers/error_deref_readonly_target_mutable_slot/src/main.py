# A wrapper whose `__deref__` returns `readonly[P]` cannot be passed at a
# plain `P` param: the param binds the wrapped object itself, so the readonly
# rule a `readonly[P]` value meets applies (a `readonly[P]` param accepts it).
from tpy import int32, readonly


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


class RO:
    _p: P

    def __init__(self) -> None:
        self._p = P(5)

    @readonly
    def __deref__(self) -> readonly[P]:
        return self._p


def bump(p: P) -> None:
    p.n += 1


def main() -> None:
    o = RO()
    bump(o)  # tpyc: error(/Cannot pass readonly\[P\] as mutable P/)


main()
