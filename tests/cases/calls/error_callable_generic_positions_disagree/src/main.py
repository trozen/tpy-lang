# Error: a callable whose declared parameter binds U to a borrow and whose
# return binds it to a new value fits no instantiation, as no hand-written
# twin of `apply_twice` takes it; the message names the owning slot.
from tpy import Fn, Own, int32


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def mk(p: P) -> Own[P]:
    return P(p.n + 1)


def apply_twice[U](f: Fn[[U], U], x: U) -> U:
    return f(f(x))


def main() -> None:
    p = P(1)
    # The subject: mk's parameter says P, its return Own[P].
    print(apply_twice(mk, p).n)  # tpyc: error(/U is P from f's parameter but Own\[P\] from f's return; write Fn\[\[U\], Own\[U\]\] if f builds a new value/)


main()
