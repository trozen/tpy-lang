# The owned-result tie-break orders a written `Own[T]` over a bare `T`; two
# variants neither of which writes `Own[T]` at the callable's result (a
# bare `T` against a concrete `Own[P]`) stay ambiguous.
from tpy import Fn, Own, dispatch, int32


class P:
    x: int32

    def __init__(self, x: int32) -> None:
        self.x = x


def make_from(x: int32) -> Own[P]:
    return P(x)


@dispatch
def g[T](f: Fn[[int32], T]) -> int32:
    return 1


@dispatch
def g[A](f: Fn[[A], Own[P]]) -> int32:
    return 2


def main() -> None:
    print(g(make_from))  # tpyc: error(/Ambiguous overload for 'g'/)


main()
