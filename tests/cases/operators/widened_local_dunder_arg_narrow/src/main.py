# A resolved binop's fixed-int PARAM slot takes the same checked narrow as any
# call argument, and it keys on the operand's DECLARED type: a literal-seeded
# local retro-widened to BigInt by a later assignment must narrow at the
# forward operand, the reflected operand and the `__contains__` needle even
# though sema types each occurrence int32.
from tpy import int32


class Bag:
    xs: list[int32]

    def __init__(self) -> None:
        self.xs = [1, 2, 3]

    def __contains__(self, k: int32) -> bool:
        return k == 1

    def __add__(self, k: int32) -> int32:
        return k + 1

    def __radd__(self, k: int32) -> int32:
        return k + 2


def widen() -> int:
    return 4


def needle(b: Bag) -> None:
    p = 1
    print(p in b)        # __contains__ needle
    p = widen()          # the assignment that retro-widens `p` to BigInt
    print(p)


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


def main() -> None:
    needle(Bag())
    forward(Bag())
    reflected(Bag())


main()
