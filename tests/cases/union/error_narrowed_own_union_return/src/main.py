# A narrowed `Own` union parameter returned at the same union is refused,
# never copied unwarned: no move takes the parameter out of the member's
# alias (BUGS.md#dying-owned-value-staged-result-copies).
from tpy import Own, int32


class A:
    xs: list[int32]

    def __init__(self, n: int32) -> None:
        self.xs = [n]


def own_same(u: Own[A | int32]) -> Own[A | int32]:  # tpyc: warning(/never consumed/)
    if isinstance(u, A):
        return u  # tpyc: error(/not yet supported/)
    return 0


def main() -> None:
    q = own_same(A(3))
    if isinstance(q, A):
        print(q.xs[0])


main()
