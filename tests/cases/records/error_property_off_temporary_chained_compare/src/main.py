# A chained comparison evaluates a middle operand once by binding it, and
# that binding is a statement of its own: storage the operand reads off a
# temporary dies before the pairs compare. Per docs/LANGUAGE_FEATURES.md
# (Properties, "A chained comparison"), a plain scalar there is copied and
# anything else -- this record field of a temporary's getter -- is refused,
# where the plain comparison `mk().ch.p < P(3)` reads it in place.
# WORKAROUND: bind it first -- `h = mk()` then `P(1) < h.ch.p < P(3)`.
from tpy import Own, int32


class P:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __lt__(self, o: "P") -> bool:
        return self.n < o.n


class Child:
    p: P

    def __init__(self) -> None:
        self.p = P(2)


class H:
    child: Child

    def __init__(self) -> None:
        self.child = Child()

    @property
    def ch(self) -> Child:
        return self.child


def mk() -> Own[H]:
    return H()


def main() -> None:
    print(P(1) < mk().ch.p < P(3))  # tpyc: error(/lends_from_temporary/)


main()
