# A read-only owned-element tuple param warns "never consumed" (like a scalar
# Own[T]); the escape hatch is the borrow form tuple[A, A], which does not
# warn (read_borrow). The warning is suppressed for @nocopy elements
# (consume-by-drop is legitimate), so read_nocopy does not warn either.
from tpy import Own, nocopy, int32


class A:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n


@nocopy
class B:
    m: int32

    def __init__(self, m: int32) -> None:
        self.m = m


def read_owned(p: tuple[Own[A], Own[A]]) -> int32:  # tpyc: warning(/owned tuple param 'p' is never consumed/)
    return p[0].n + p[1].n


def read_borrow(p: tuple[A, A]) -> int32:  # tpyc: ok
    return p[0].n + p[1].n


def read_nocopy(p: tuple[Own[B], Own[B]]) -> int32:  # tpyc: ok
    return p[0].m + p[1].m


def main() -> None:
    print(read_owned((A(1), A(2))))
    a = A(3)
    b = A(4)
    print(read_borrow((a, b)))
    print(read_nocopy((B(5), B(6))))


main()
