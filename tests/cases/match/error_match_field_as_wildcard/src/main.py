# A field sub-pattern spelled `(_ as x)`: the wildcard and the capture bind
# the same field twice, which the arm's condition cannot express.
from tpy import int32, Own


class X:
    k: int32

    def __init__(self) -> None:
        self.k = 5


class B:
    m: int32

    def __init__(self) -> None:
        self.m = 0


class A:
    n: X

    def __init__(self, n: Own[X]) -> None:
        self.n = n


class W:
    v: A | B

    def __init__(self) -> None:
        self.v = B()


def f(w: W) -> int32:
    match w:  # tpyc: error(/stmt\.match/)
        # The field sub-pattern is a wildcard AND a capture.
        case W(v=(_ as x)):
            return 1
    return 0


def main() -> None:
    print(f(W()))


main()
