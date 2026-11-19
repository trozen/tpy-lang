# Error: two callable arguments at one type parameter's slots disagree -- one
# returns a borrow (`U = P`), the other a new value (`U = Own[P]`); the
# message names each argument by its own position and the owning one's fix.
from tpy import Fn, Own, int32


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


def identity(p: P) -> P:
    return p


def mk(p: P) -> Own[P]:
    return P(p.n + 1)


def both[T, U](f: Fn[[T], U], g: Fn[[T], U], x: T) -> U:
    f(x)
    return g(x)


def main() -> None:
    # The subject: identity binds U = P from f's return, mk Own[P] from g's.
    print(both(identity, mk, P(1)).n)  # tpyc: error(/U is P from f's return but Own\[P\] from g's return; write Fn\[\[T\], Own\[U\]\] if g builds a new value/)


main()
