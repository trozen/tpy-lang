# A resolved binop's fixed-int PARAM slot takes the same checked narrow as any
# call argument: a literal-seeded local a later assignment makes an `int` is
# an `int` at the forward operand, the reflected operand and a composite
# operand over it, each narrowed into the dunder's int32 parameter. (An `in`
# operand is not deferred: int/error_pending_local_settled_by_membership.)
from tpy import int32


class Bag:
    xs: list[int32]

    def __init__(self) -> None:
        self.xs = [1, 2, 3]

    def __add__(self, k: int32) -> int32:
        return k + 1

    def __radd__(self, k: int32) -> int32:
        return k + 2


def widen() -> int:
    return 4


def forward(b: Bag) -> None:
    p = 1
    print(b + p)         # __add__ argument slot
    p = widen()
    print(p)


def reflected(b: Bag) -> None:
    p = 1
    print(p + b)         # __radd__ argument slot (the operands swap)
    p = widen()
    print(p)


def composite(b: Bag) -> None:
    p = 0
    print(b + (p + 1))   # __add__ argument slot, over an operation on `p`
    p = widen()
    print(p)


def main() -> None:
    forward(Bag())
    composite(Bag())
    reflected(Bag())


main()
