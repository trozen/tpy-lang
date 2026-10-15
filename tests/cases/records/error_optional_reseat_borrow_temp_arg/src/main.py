# An Optional reseated in a block from a call that lends from an argument
# temporary: the temporary dies with the block while the pointer outlives it,
# so the reseat is refused, as the plain pointer reseat's is
# (BUGS.md#optional-reseat-borrow-of-temporary-rejects).
from tpy import Own


class P:
    def __init__(self, n: int) -> None:
        self.n = n


def first(xs: list[P]) -> P | None:
    if len(xs) > 0:
        return xs[0]
    return None


def mkl(n: int) -> Own[list[P]]:
    return [P(n), P(n + 1)]


def main(c: bool) -> None:
    z: P | None = None
    if c:
        z = first(mkl(7))  # tpyc: warning(/temporary argument 'xs'/) error(/call\.arg_shape\.container/)
    if z is not None:
        print(z.n)


main(True)
