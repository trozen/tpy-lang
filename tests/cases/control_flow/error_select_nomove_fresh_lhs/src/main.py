# An and/or whose fresh LHS is a @nomove record cannot hand the chosen LHS
# to an owning slot: it is evaluated once into a temp and would have to be
# moved out. The ternary of fresh @nomove values is fine (no move needed).
from tpy import int32, Own, nomove


@nomove
class M:
    n: int32

    def __init__(self, n: int32) -> None:
        self.n = n

    def __bool__(self) -> bool:
        return self.n != 0


def pick(c: bool) -> Own[M]:
    return M(1) if c else M(2)


def first() -> Own[M]:
    # The subject: the chosen `M(0)` would leave its temp by a move.
    return M(0) or M(5)  # tpyc: error(/not yet supported/)


def main() -> None:
    print(pick(True).n, first().n)


main()
