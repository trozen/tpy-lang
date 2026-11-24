# A narrowed `Own` union parameter returned as its member: the narrowed
# read is the member's alias, which nothing moves the parameter out of, so
# the return is refused rather than copied unwarned
# (BUGS.md#dying-owned-value-staged-result-copies).
from tpy import Own, int32


class A:
    xs: list[int32]

    def __init__(self, n: int32) -> None:
        self.xs = [n]


def take(u: Own[A | int32]) -> Own[A]:  # tpyc: warning(/never consumed/)
    if isinstance(u, A):
        return u  # tpyc: error(/not yet supported/)
    return A(0)


def main() -> None:
    a = take(A(3))
    print(len(a.xs))


main()
